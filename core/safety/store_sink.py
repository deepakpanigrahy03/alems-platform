"""
core/safety/store_sink.py: writes safety guard records to the store.

The guard never waits for the store: episode, event and sample records are
queued in memory and written through the store writer (InProcessWriter, one
batch per flush) on the main thread, just before and just after each
admission (core.safety.hook) and at close. That is always between runs, so
the guard adds no store I/O inside a measurement window, and the writer is
never used from a second thread (a shared SQLite connection refuses that). A failed batch is kept and retried at the next flush.
Event and sample inserts are idempotent (INSERT OR IGNORE on their keys), so a
retried batch never duplicates rows. The host log line is written by the guard
before an event reaches this queue.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EPISODE_COLS = ("episode_id", "entry_path", "pid", "started_utc_ns", "started_mono_ns", "ended_utc_ns",
                "ended_mono_ns", "seq_session_id", "te_session_id", "policy_version", "config_json",
                "config_sha256", "status", "latched", "latch_reason", "end_reason")
EPISODE_UPDATE = ("ended_utc_ns", "ended_mono_ns", "status", "latched", "latch_reason", "end_reason")
EVENT_COLS = ("event_id", "episode_id", "seq", "event_type", "utc_ns", "mono_ns", "value_c", "threshold_c",
              "source", "component", "limit_type", "quality", "exp_id", "run_id", "goal_id", "attempt_no",
              "seq_attempt_id", "te_session_id", "detail_json", "log_written")
SAMPLE_COLS = ("episode_id", "seq", "utc_ns", "mono_ns", "min_headroom_c", "min_source", "min_component",
               "min_limit_type", "min_quality", "gpu_temp_c", "gpu_power_mw", "gpu_power_limit_mw",
               "clock_reasons_bitmask", "rise_c_per_s", "readings_json")


def _insert(table, cols, verb="INSERT"):
    # type: (str, Tuple[str, ...], str) -> str
    """INSERT statement with an explicit column list (never positional, MSC-6 style)."""
    return "%s INTO %s (%s) VALUES (%s)" % (verb, table, ", ".join(cols), ", ".join("?" * len(cols)))


SQL = {
    "episode_insert": _insert("safety_guard_episodes", EPISODE_COLS, "INSERT OR IGNORE"),
    "episode_update": "UPDATE safety_guard_episodes SET %s WHERE episode_id = ?"
                      % ", ".join("%s = ?" % c for c in EPISODE_UPDATE),
    "event": _insert("safety_guard_events", EVENT_COLS, "INSERT OR IGNORE"),
    "sample": _insert("safety_guard_samples", SAMPLE_COLS, "INSERT OR IGNORE"),
}


def host_log_path(store):
    # type: (str) -> Optional[str]
    """alems.jsonl in the host log directory (ALEMS_LOG_DIR), else next to the store; None if neither."""
    try:
        from core.observability.locations import host_log_dir, run_log_dir
        d = host_log_dir() or run_log_dir(store)
        return str(Path(d) / "alems.jsonl")
    except Exception as exc:
        logger.error("safety guard: no host log directory (%s); events go to the store only", exc)
        return None


class StoreSink(object):
    """Queue of guard records, flushed between runs by its own thread."""

    def __init__(self, writer_factory):
        # type: (Callable[[], Any]) -> None
        """writer_factory returns a fresh store writer; one is opened per flush, never kept open."""
        self._writer_factory = writer_factory
        self._queue = []           # type: List[Tuple[str, tuple]]
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._guard = None         # type: Optional[Any]
        self.failed_batches = 0

    def attach(self, guard):
        # type: (Any) -> None
        """Remember the guard; flushing is driven by the caller on the main thread."""
        self._guard = guard

    # guard interface (never blocks)
    def store_episode(self, ep):
        # type: (Dict[str, Any]) -> None
        """Queue the episode row (written before any of its events)."""
        self._put("episode_insert", tuple(ep[c] for c in EPISODE_COLS))

    def update_episode(self, ep):
        # type: (Dict[str, Any]) -> None
        """Queue an update of the episode end, status and latch fields."""
        self._put("episode_update", tuple(ep[c] for c in EPISODE_UPDATE) + (ep["episode_id"],))

    def store_event(self, ev):
        # type: (Dict[str, Any]) -> None
        """Queue one event row (its host log line is already written)."""
        self._put("event", tuple(ev[c] for c in EVENT_COLS))

    def store_sample(self, row):
        # type: (Dict[str, Any]) -> None
        """Queue one watchdog sample row."""
        self._put("sample", tuple(row[c] for c in SAMPLE_COLS))

    def _put(self, kind, params):
        # type: (str, tuple) -> None
        """Append to the queue under the lock; the guard never waits for the store."""
        with self._lock:
            self._queue.append((kind, params))

    def pending(self):
        # type: () -> int
        """Records waiting to be written."""
        with self._lock:
            return len(self._queue)

    def flush(self):
        # type: () -> bool
        """Write everything queued in one batch, in order; on failure keep it for the next flush."""
        with self._flush_lock:
            with self._lock:
                items, self._queue = self._queue, []
            if not items:
                return True
            try:
                self._write(items)
                return True
            except Exception as exc:
                self._requeue(items, exc)
                return False

    def _write(self, items):
        # type: (List[Tuple[str, tuple]]) -> None
        """One writer, one transaction, records in queue order (episode rows precede their events)."""
        w = self._writer_factory()
        w.open()
        try:
            self._execute_all(w, items)
        finally:
            w.close()

    @staticmethod
    def _execute_all(w, items):
        # type: (Any, List[Tuple[str, tuple]]) -> None
        """All records inside one batch: committed together or rolled back together."""
        with w.batch():
            for kind, params in items:
                w.execute(SQL[kind], params)

    def _requeue(self, items, exc):
        # type: (List[Tuple[str, tuple]], Exception) -> None
        """Keep a failed batch at the front of the queue; inserts are idempotent, so a retry is safe."""
        self.failed_batches += 1
        logger.error("safety guard: store write failed (%d records kept): %s", len(items), exc)
        with self._lock:
            self._queue = items + self._queue

    def close(self):
        # type: () -> None
        """Write what is left."""
        if not self.flush():
            logger.error("safety guard: %d records not stored at close (host log has the events)",
                         self.pending())
