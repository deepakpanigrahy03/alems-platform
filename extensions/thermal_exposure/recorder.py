"""
Continuous experiment recorder (thermal_exposure extension).

Records temperature, cooling, GPU temperature, power rails and room
temperature for a whole experiment: a short pre-window before the first run,
every gap between runs and an optional cool-down after the last run.

Ownership of samples:
  * inside a run the platform's own per-run samplers record as before and
    write to the core sample tables under the run's run_id;
  * outside runs this recorder writes to the te_*_samples tables, keyed to
    one te_session row. The recorder is paused for the whole run, so every
    sample is stored exactly once.

Time line: at open the wall clock and the monotonic clock are read together.
Every sample time is  wall_at_open + (monotonic_now - monotonic_at_open),
so the stored time line never jumps when the system clock is corrected.
Clock anchors (every anchor_s seconds) record wall and monotonic time side by
side; they detect and document clock steps and never change sample times.

This module imports nothing from core: readers and the writer are passed in
(the platform builds them), following the extension rule that extensions
import only alems_sdk.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from extensions.thermal_exposure.session_spans import (
    RECOVER_PLACEMENTS_SQL, RECOVER_SPANS_SQL, SessionSpans, partition_problems)
from extensions.thermal_exposure.vocabulary import THERMAL_SESSION
from extensions.thermal_exposure.idle_session import IdleSession

logger = logging.getLogger(__name__)

CLOCK_BASIS = "wall_at_open_plus_monotonic_v1"
PROFILE_ID = "experiment_recording"
PACING_PLUGIN = "none"

# source name -> (te table, column order). The order matches _to_row().
TABLES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "THERMAL_ZONE": ("te_thermal_samples",
                     ("session_id", "zone_id", "timestamp_ns", "temp_celsius",
                      "quality_flag", "invalid_reason")),
    "COOLING": ("te_cooling_samples",
                ("session_id", "device_id", "timestamp_ns", "cur_state",
                 "quality_flag", "invalid_reason")),
    "GPU_TEMP": ("te_gpu_temp_samples",
                 ("session_id", "gpu_index", "timestamp_ns", "temperature_c",
                  "source", "quality_flag", "invalid_reason")),
    "POWER_RAIL": ("te_power_rail_samples",
                   ("session_id", "rail_id", "timestamp_ns", "interval_ns",
                    "power_mw", "quality_flag", "invalid_reason")),
    "AMBIENT": ("te_ambient_samples",
                ("session_id", "sensor_name", "timestamp_ns", "temp_celsius",
                 "quality_flag", "invalid_reason")),
    # cumulative energy counters, read only inside idle windows (idle_session)
    # catalog sensors of the platform reader (Apple Silicon dies, x86 coretemp, ...)
    "SENSORS": ("te_sensor_samples",
                ("session_id", "catalog_id", "timestamp_ns", "temp_celsius",
                 "quality_flag", "catalog_sha256")),
    "ENERGY": ("te_energy_samples",
               ("session_id", "domain_id", "timestamp_ns", "energy_uj",
                "quality_flag", "invalid_reason")),
}

# key column per source: used to write READ_FAILED rows for the same
# sensors after a whole-reader failure
_KEY = {"THERMAL_ZONE": "zone_id", "COOLING": "device_id", "GPU_TEMP": "gpu_index",
        "POWER_RAIL": "rail_id", "AMBIENT": "sensor_name", "ENERGY": "domain_id",
        "SENSORS": "catalog_id"}
# value column per source: NULL when the read failed (MIC-1, never 0)
# global_session_ids of recordings open in this process: recovery never aborts them
_OPEN_IN_PROCESS: set = set()

# seconds after a run ends before the recorder writes again, so a run saving
# its own data never waits behind the recorder for the store lock
RESUME_SETTLE_S = 3.0

# a run slot is linked to the runs row whose start_time_ns lies within this
# distance of the slot start (pause is aligned to the run's own start stamp;
# measured mapping error about 0.001 ms, shortest real run far above 5 ms)
SLOT_MATCH_TOLERANCE_NS = 5_000_000

_VALUE = {"THERMAL_ZONE": "temp_celsius", "COOLING": "cur_state",
          "GPU_TEMP": "temperature_c", "POWER_RAIL": "power_mw",
          "AMBIENT": "temp_celsius", "ENERGY": "energy_uj", "SENSORS": "temp_celsius"}


class SystemClock:
    """Wall and monotonic clocks; replaced by a fake clock in tests."""

    def wall_ns(self) -> int:
        """Wall clock in epoch nanoseconds (may be stepped by NTP)."""
        return time.time_ns()

    def mono_ns(self) -> int:
        """Monotonic clock in nanoseconds (never steps)."""
        return time.monotonic_ns()


def read_timesync_offset_ns() -> Tuple[Optional[int], Optional[str]]:
    """
    NTP offset from `timedatectl timesync-status` (no sudo).

    Returns:
        (offset in ns or None when unavailable, method name or None).
    """
    try:
        out = subprocess.run(["timedatectl", "timesync-status"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("timedatectl unavailable: %s", exc)
        return None, None
    m = re.search(r"Offset:\s*([+-]?[\d.]+)\s*(ns|us|ms|s)?", out)
    if not m:
        return None, "timedatectl"
    scale = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000, None: 1}
    return int(float(m.group(1)) * scale[m.group(2)]), "timedatectl"


class _SourceStats:
    """Per-source counters for the rate and gap checks."""

    def __init__(self, hz: float) -> None:
        self.hz = hz
        self.ticks = 0
        self.emitted = 0
        self.errors = 0
        self.max_gap_ns = 0
        self.active_ns = 0          # recording time while not paused
        self.intervals = 0          # read-to-read intervals counted in active_ns
        self.last_ns: Optional[int] = None


class ExperimentRecorder:
    """
    Records between-runs samples for one experiment into the te_ tables.

    Args:
        readers: source name -> callable returning a list of row dicts
                 (the platform's reader output; its own timestamps are ignored).
        writer_factory: returns a fresh, unopened writer with open(), close(),
                 batch(), execute(), execute_many(), last_row_id().
        rates_hz: source name -> sampling rate from the sampling policy.
        node_name: logical node name (never a hostname).
        machine_profile: logical machine profile name.
        pre_window_s: seconds recorded before open() returns (first run waits).
        cooldown: None, or {"max_s", "tolerance_c", "hold_s"} for the
                 optional cool-down after the last run.
        anchor_s / flush_s: clock anchor and write intervals.
        clock: SystemClock or a test double.
        settings: the experiment's recording section (idle, cooldown, checks,
                 server); rules and defaults live in idle_session / idle_checks.
    """

    def __init__(self, readers: Dict[str, Callable[[], List[Dict]]],
                 writer_factory: Callable[[], Any], rates_hz: Dict[str, float],
                 node_name: str = "local", machine_profile: str = "unknown",
                 pre_window_s: float = 30.0, cooldown: Optional[Dict] = None,
                 anchor_s: float = 10.0, flush_s: float = 2.0,
                 clock: Optional[SystemClock] = None,
                 offset_fn: Callable[[], Tuple[Optional[int], Optional[str]]] = read_timesync_offset_ns,
                 settings: Optional[Dict[str, Any]] = None
                 ) -> None:
        self.readers = dict(readers)
        if "AMBIENT" not in self.readers and "AMBIENT" in rates_hz:
            # room probe is this extension's own reader (UNAVAILABLE until fitted)
            from extensions.thermal_exposure.ambient_reader import AmbientReader
            _amb = AmbientReader()
            self.readers["AMBIENT"] = lambda: [_amb.read()]
        readers = self.readers
        self.writer_factory = writer_factory
        self.rates = {k: float(v) for k, v in rates_hz.items() if k in readers}
        self.node_name = node_name
        self.machine_profile = machine_profile
        self.pre_window_s = pre_window_s
        self.cooldown = cooldown
        self.anchor_s = anchor_s
        self.flush_s = flush_s
        self.clock = clock or SystemClock()
        self.offset_fn = offset_fn
        self.session_id: Optional[int] = None
        # one lock guards pause state, buffers and stats
        self._lock = threading.Lock()
        self._paused = False
        self._pause_ts: Optional[int] = None
        self._stop = threading.Event()
        # set while recording, cleared during a run: paused threads block on it
        # instead of waking every few ms, so a run gets the CPU to itself
        self._active = threading.Event()
        self._active.set()
        self._threads: List[threading.Thread] = []
        self._buffers: Dict[str, List[tuple]] = {k: [] for k in self.rates}
        self._stats = {k: _SourceStats(hz) for k, hz in self.rates.items()}
        self._last_keys: Dict[str, List[Any]] = {k: [] for k in self.rates}
        self._anchors: List[Tuple[int, int]] = []       # (drift_ns, stepped)
        self._runs = 0
        self._workload_start: Optional[int] = None
        self._workload_end: Optional[int] = None
        self._closed = False
        self._gen = 0                       # bumped at every pause: detects in-flight reads
        self._resume_mono: Optional[int] = None
        self._fail_counts: Dict[str, int] = {k: 0 for k in self.rates}
        self._gsid: Optional[str] = None
        # spans of this recording (vocabulary thermal_session)
        self._spans: Optional[SessionSpans] = None
        self._span_ids: Dict[str, str] = {}
        self._slots: List[Tuple[str, int]] = []      # (run slot span id, run number)
        self._gap_start: Optional[int] = None
        # idle reference, cooling rule and recording checks (idle_session.py)
        self.idle = IdleSession(self, settings or {})

    # ── time line ────────────────────────────────────────────────────────────

    def _ts(self) -> int:
        """Session time: wall at open plus monotonic time elapsed since open."""
        return self._base_wall + (self.clock.mono_ns() - self._base_mono)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> int:
        """
        Recover stale sessions, create the te_session row, start the threads
        and record the pre-window. Returns the session id.
        """
        _retry(lambda: recover_open_sessions(self.writer_factory, self.node_name),
               "stale recording recovery")
        # both clocks read together; this pair defines the session time line
        self._base_wall = self.clock.wall_ns()
        self._base_mono = self.clock.mono_ns()
        self.session_id = self._insert_session()
        self._open_spans()
        self.idle.prepare()              # names, carry-over, energy mapping; never raises
        for name in self.rates:
            self._spawn(self._source_loop, name, "rec-%s" % name.lower())
        self._spawn(self._anchor_loop, None, "rec-anchor")
        self._spawn(self._flush_loop, None, "rec-flush")
        logger.info("recording opened: session %d, sources %s, pre-window %.0f s",
                    self.session_id, ",".join(sorted(self.rates)), self.pre_window_s)
        self.idle.wait_pre_window()                   # first run starts after this
        return self.session_id

    def _spawn(self, target: Callable, arg: Any, name: str) -> None:
        """Start one daemon thread (daemon: a crash must not hang the process)."""
        t = threading.Thread(target=target, args=(arg,) if arg is not None else (),
                             name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def pause(self) -> None:
        """A run starts: stop storing samples from this instant (run owns them)."""
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._gen += 1
            self._active.clear()
            self._pause_ts = self._ts()
            self._runs += 1
            if self._workload_start is None:
                self._workload_start = self._pause_ts
            self._span_pause(self._pause_ts)      # in memory only; written after the run
            for st in self._stats.values():
                st.last_ns = None        # the run is not a gap of this recorder

    def resume(self) -> None:
        """A run ended: samples belong to the recording again."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            self._workload_end = self._ts()
            self._pause_ts = None
            self._span_resume(self._workload_end)
            self._resume_mono = self.clock.mono_ns()
            self._active.set()

    def close(self, status: str = "closed", reason: Optional[str] = None) -> None:
        """Optional cool-down, stop threads, final write, close the session row."""
        if self._closed or self.session_id is None:
            return
        self._closed = True
        if self._paused:
            # a run raised before its end notification: never cool down on stale data
            logger.warning("recording session %d closed while paused (run ended without "
                           "notification)", self.session_id)
            self.resume()
            if status == "closed":
                reason = reason or "closed while a run was still open"
        cooldown_status = self.idle.run_cooldown() if status == "closed" else "aborted"
        post = self.idle.run_post_window(cooldown_status) if status == "closed" else None
        self._stop.set()
        self._active.set()            # wake any thread blocked on a pause
        for t in self._threads:
            t.join(timeout=5.0)
            if t.is_alive():
                logger.warning("recorder thread %s still running at close", t.name)
        self._resume_mono = None      # no settle delay for the final writes
        end_ns = self._ts()
        self._finish_spans(status, cooldown_status, end_ns, post)
        _retry(self._flush_strict, "final sample write")
        _retry(lambda: self._finish_session(status, reason, cooldown_status, end_ns),
               "session close")
        _retry(lambda: self.idle.write_results(end_ns), "idle results")
        _retry(lambda: self._write_checks(end_ns), "session checks")
        dropped = sum(len(v) for v in self._buffers.values())
        if dropped:
            logger.error("recording session %d: %d samples could not be written",
                         self.session_id, dropped)
        _OPEN_IN_PROCESS.discard(self._gsid)
        logger.info("recording closed: session %d, status %s, runs %d, cool-down %s",
                    self.session_id, status, self._runs, cooldown_status)

    # ── sampling ─────────────────────────────────────────────────────────────

    def _source_loop(self, name: str) -> None:
        """Fixed-step monotonic schedule (no drift), like the engine loop."""
        interval_ns = int(1e9 / self.rates[name])
        next_due = self.clock.mono_ns()
        while not self._stop.is_set():
            if not self._active.is_set():
                self._active.wait()                       # sleep for the whole run
                next_due = self.clock.mono_ns()           # restart the schedule at resume
                continue
            wait_ns = next_due - self.clock.mono_ns()
            if wait_ns > 0:
                self._stop.wait(min(0.05, wait_ns / 1e9))
                continue
            self._tick(name, interval_ns)
            next_due += interval_ns
            if self.clock.mono_ns() - next_due > interval_ns:
                next_due = self.clock.mono_ns() + interval_ns   # stall: restart, gap kept

    def _tick(self, name: str, interval_ns: int) -> None:
        """One read of one source; never lets an error end the sampling thread."""
        try:
            self._tick_once(name, interval_ns)
        except Exception:                             # malformed reader output etc.
            logger.exception("recorder %s tick failed; source keeps running", name)

    def _tick_once(self, name: str, interval_ns: int) -> None:
        """One read; rows stamped with the read-start time."""
        if self._paused:
            return
        if name == "ENERGY" and not self.idle.energy_active():
            return                       # counters are read only inside idle windows
        gen, t0 = self._gen, self._ts()
        try:
            rows, failed = self.readers[name]() or [], None
        except Exception as exc:                      # a failed read never stops recording
            rows, failed = [], str(exc)[:200]
            logger.warning("recorder %s read failed: %s", name, exc)
        with self._lock:
            if gen != self._gen or self._paused:
                # a run began during this read: keep the rows only if the read
                # started before the run, and never count it in the gap statistics
                if self._pause_ts is None or t0 >= self._pause_ts or failed is not None:
                    return
            else:
                self._account(name, t0, interval_ns, failed)
            if failed is not None:
                rows = [{_KEY[name]: k, "quality_flag": "READ_FAILED",
                         "invalid_reason": failed} for k in self._last_keys[name]]
            elif rows:
                self._last_keys[name] = [r.get(_KEY[name]) for r in rows]
            for r in rows:
                self._buffers[name].append(self._to_row(name, r, t0))
                self.idle.observe(name, r, t0)

    def _account(self, name: str, t0: int, interval_ns: int, failed: Optional[str]) -> None:
        """Update rate and gap counters (caller holds the lock)."""
        st = self._stats[name]
        st.ticks += 1
        if failed is not None:
            st.errors += 1
        else:
            st.emitted += 1
        if st.last_ns is not None:
            gap = t0 - st.last_ns
            st.max_gap_ns = max(st.max_gap_ns, gap)
            st.active_ns += gap
            st.intervals += 1
        st.last_ns = t0

    def _to_row(self, name: str, r: Dict, ts: int) -> tuple:
        """Map one reader dict to the te table column order."""
        if name == "SENSORS":
            return self._sensor_row(r, ts)
        flag = r.get("quality_flag") or "VALID"
        value = r.get(_VALUE[name])
        if flag == "UNAVAILABLE" and name != "AMBIENT":
            flag = "MISSING"          # only the room table knows UNAVAILABLE
        if flag in ("MISSING", "READ_FAILED", "UNAVAILABLE"):
            value = None     # core readers put 0 here; a failed read is NULL (MIC-1)
        elif value is None:
            flag = "MISSING"          # a VALID flag needs a value (table CHECK)
        sid, reason = self.session_id, r.get("invalid_reason")
        if name == "THERMAL_ZONE":
            return (sid, r["zone_id"], ts, value, flag, reason)
        if name == "COOLING":
            return (sid, r["device_id"], ts, value, flag, reason)
        if name == "GPU_TEMP":
            return (sid, r.get("gpu_index", 0), ts, value, r.get("source") or "unknown",
                    flag, reason)
        if name == "POWER_RAIL":
            return (sid, r["rail_id"], ts, r.get("interval_ns"), value, flag, reason)
        if name == "ENERGY":
            return (sid, r["domain_id"], ts, value, flag, reason)
        return (sid, r.get("sensor_name") or "ambient", ts, value, flag, reason)


    # ── clock anchors ────────────────────────────────────────────────────────

    def _sensor_row(self, r: Dict, ts: int) -> tuple:
        """te_sensor_samples row; a whole-reader failure arrives as READ_FAILED."""
        flag = r.get("quality_flag") or "READ_FAILED"
        value = r.get("temp_celsius") if flag in ("VALID", "OUT_OF_RANGE") else None
        return (self.session_id, r["catalog_id"], ts, value, flag, r.get("catalog_sha256"))

    def _anchor_loop(self) -> None:
        """Write one clock anchor at open and then every anchor_s seconds."""
        prev_drift: Optional[int] = None
        while True:
            if not self._active.is_set():
                self._active.wait()       # no subprocess or store write during a run
                if self._stop.is_set():
                    self._write_anchor(prev_drift)
                    return
                if self._stop.wait(RESUME_SETTLE_S):   # let the run save first
                    self._write_anchor(prev_drift)
                    return
            prev_drift = self._write_anchor(prev_drift)
            if self._stop.wait(self.anchor_s):
                self._write_anchor(prev_drift)        # final anchor at close
                return

    def _write_anchor(self, prev_drift: Optional[int]) -> int:
        """Anchor = wall and monotonic read together; drift = wall - session time."""
        wall, mono = self.clock.wall_ns(), self.clock.mono_ns()
        drift = wall - (self._base_wall + (mono - self._base_mono))
        step_ns = abs(drift - prev_drift) if prev_drift is not None else 0
        stepped = 1 if step_ns > 1_000_000 else 0     # >1 ms jump between anchors
        offset, method = self.offset_fn()
        with self._lock:
            self._anchors.append((drift, step_ns if stepped else 0))
        try:
            self._write_rows("INSERT INTO te_clock_anchor (session_id, node_name, wall_ns, "
                             "mono_ns, sync_offset_ns, sync_method, offset_to_control_ns, "
                             "stepped) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                             [(self.session_id, self.node_name, wall, mono, offset, method,
                               stepped)])
        except Exception as exc:          # store busy: the anchor is kept in memory checks
            logger.warning("clock anchor write failed: %s", exc)
        return drift

    # ── writing ──────────────────────────────────────────────────────────────

    def _flush_loop(self) -> None:
        """Write buffered samples every flush_s seconds; log progress each minute."""
        last_log = self.clock.mono_ns()
        while not self._stop.wait(self.flush_s):
            if not self._active.is_set():
                self._active.wait()       # rows read before the run stay buffered until after it
                continue
            if self._settling():
                continue                  # the run is saving; write a few seconds later
            self._flush()
            if self.clock.mono_ns() - last_log >= 60_000_000_000:
                last_log = self.clock.mono_ns()
                logger.info("recording session %d: %.0f s, runs %d, rows %s",
                            self.session_id, (self._ts() - self._base_wall) / 1e9,
                            self._runs, {k: s.emitted for k, s in self._stats.items()})

    def _settling(self) -> bool:
        """True for RESUME_SETTLE_S seconds after a run ended."""
        r = self._resume_mono
        return r is not None and self.clock.mono_ns() - r < RESUME_SETTLE_S * 1e9

    def _flush(self) -> None:
        """Write buffered samples, one batch per source; never raises."""
        try:
            self._flush_strict()
        except Exception as exc:
            logger.warning("recorder flush failed: %s", exc)

    def _flush_strict(self) -> None:
        """Write each source's rows; raise if any source's rows had to be kept."""
        with self._lock:
            pending = {k: v for k, v in self._buffers.items() if v}
            self._buffers = {k: [] for k in self.rates}
        kept = 0
        for name, rows in pending.items():
            if not self._write_source(name, rows):
                kept += len(rows)
                with self._lock:
                    self._buffers[name] = rows + self._buffers[name]
        spans_ok = self._flush_spans()     # between runs only, like the samples
        if kept or not spans_ok:
            raise RuntimeError("%d rows kept for the next write%s"
                               % (kept, "" if spans_ok else "; spans kept"))

    def _write_source(self, name: str, rows: List[tuple]) -> bool:
        """
        One batch for one source. After three failed batches the rows are
        written one by one and rows the table refuses are dropped and logged,
        so one bad row can never block a source for the rest of the session.
        """
        table, cols = TABLES[name]
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(cols),
                                                   ", ".join("?" * len(cols)))
        try:
            self._write_rows(sql, rows)
            self._fail_counts[name] = 0
            return True
        except Exception as exc:          # store busy or a refused row
            self._fail_counts[name] += 1
            logger.warning("recorder %s write failed (%d): %s", name,
                           self._fail_counts[name], exc)
            if self._fail_counts[name] < 3:
                return False
        bad = 0
        for row in rows:
            try:
                self._write_rows(sql, [row])
            except Exception as exc:
                bad += 1
                logger.error("recorder %s row dropped: %s (%s)", name, row, exc)
        self._fail_counts[name] = 0
        return True

    def _write_rows(self, sql: str, rows: List[tuple]) -> None:
        """Write a few rows immediately (session row, anchors, checks)."""
        w = self.writer_factory()
        w.open()
        try:
            with w.batch():
                w.execute_many(sql, rows)
        finally:
            w.close()

    def _insert_session(self) -> int:
        """Create the te_session row (run_id NULL: no row in runs)."""
        cfg = {"pre_window_s": self.pre_window_s, "cooldown": self.cooldown,
               "rates_hz": self.rates, "anchor_s": self.anchor_s}
        sha = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
        gsid = "%s-%d-%d" % (self.node_name, os.getpid(), self._base_wall)
        self._gsid = gsid
        _OPEN_IN_PROCESS.add(gsid)
        return _retry(lambda: self._insert_session_row(gsid, sha), "session open", raise_last=True)

    def _insert_session_row(self, gsid: str, sha: str) -> int:
        """One attempt to insert the te_session row."""
        w = self.writer_factory()
        w.open()
        try:
            with w.batch():
                w.execute("INSERT INTO te_session (global_session_id, node_name, role, "
                          "exp_id, run_id, profile_id, profile_sha256, pacing_plugin, "
                          "machine_profile, clock_basis, status, start_ns, pre_window_ns) "
                          "VALUES (?, ?, 'full', NULL, NULL, ?, ?, ?, ?, ?, 'open', ?, ?)",
                          (gsid, self.node_name, PROFILE_ID, sha, PACING_PLUGIN,
                           self.machine_profile, CLOCK_BASIS, self._base_wall,
                           int(self.pre_window_s * 1e9)))
                sid = w.last_row_id()
        finally:
            w.close()
        return int(sid)

    def _finish_session(self, status: str, reason: Optional[str],
                        cooldown_status: str, end_ns: int) -> None:
        """Close the session row; exp_id from the runs inside the window."""
        self._write_rows(
            "UPDATE te_session SET status = ?, status_reason = ?, end_ns = ?, "
            "workload_start_ns = ?, workload_end_ns = ?, cooldown_status = ?, "
            "exp_id = (SELECT CASE WHEN COUNT(DISTINCT exp_id) = 1 THEN MIN(exp_id) END "
            "          FROM runs WHERE start_time_ns >= ? AND start_time_ns < ?) "
            "WHERE session_id = ?",
            [(status, reason, end_ns, self._workload_start, self._workload_end,
              cooldown_status, self._base_wall, end_ns, self.session_id)])
        if self._runs:
            w = self.writer_factory()
            w.open()
            try:
                row = w.execute("SELECT exp_id FROM te_session WHERE session_id = ?",
                                (self.session_id,))
            finally:
                w.close()
            if row and row[0].get("exp_id") is None:
                logger.warning("recording session %d: runs from more than one experiment "
                               "(or none) in the window; exp_id left NULL", self.session_id)

    # ── cool-down and checks ─────────────────────────────────────────────────

# ── session spans ────────────────────────────────────────────────────────
    # Boundaries come only from the recorder's own instants: open, pause (a
    # run starts), resume (a run ends) and close. Spans are buffered and
    # written by the flush, which never writes during a run.

    def _open_spans(self) -> None:
        """Session and pre-window spans, written at once so a crash leaves them open."""
        self._spans = SessionSpans(THERMAL_SESSION, self._gsid, self.node_name)
        sid = self._spans.open("session", "recording:%d" % self.session_id, self._base_wall)
        self._span_ids = {"session": sid}
        self._span_ids["pre"] = self._spans.open("idle_window", "idle_window:pre",
                                                 self._base_wall, sid)
        _retry(self._flush_spans_strict, "span open")

    def _span_pause(self, ts: int) -> None:
        """A run starts at ts. Never raises: a span error must not break a run."""
        if self._spans is None:
            return
        try:
            self._span_pause_strict(ts)
        except Exception as exc:
            logger.error("recording span at run start failed: %s", exc)

    def _span_pause_strict(self, ts: int) -> None:
        """Close the pre-window (first run) or the gap, then open this run's slot."""
        sp, ids = self._spans, self._span_ids
        if "workload" not in ids:
            sp.close(ids["pre"], ts, self._pre_window_attrs(ts))
            ids["workload"] = sp.open("workload", "workload", ts, ids["session"])
        elif self._gap_start is not None:
            # gaps are created only once both ends are known, so the
            # tentative gap after the last run is never written
            gid = sp.open("gap", "gap:%d" % (self._runs - 1), self._gap_start, ids["workload"])
            sp.close(gid, ts, {"seconds": (ts - self._gap_start) / 1e9})
        self._gap_start = None
        ids["slot"] = sp.open("run_slot", "run_slot:%d" % self._runs, ts, ids["workload"])

    def _span_resume(self, ts: int) -> None:
        """A run ends at ts: fix the slot end (its run_id is matched at close)."""
        if self._spans is None or "slot" not in self._span_ids:
            return
        try:
            slot = self._span_ids.pop("slot")
            self._spans.set_end(slot, ts)
            self._slots.append((slot, self._runs))
            self._gap_start = ts
        except Exception as exc:
            logger.error("recording span at run end failed: %s", exc)

    def _pre_window_attrs(self, end_ns: int) -> Dict[str, Any]:
        """Attributes of the pre-window idle span ending at end_ns."""
        return {"purpose": "pre", "planned_s": float(self.pre_window_s),
                "actual_s": (end_ns - self._base_wall) / 1e9}

    def _finish_spans(self, status: str, cooldown_status: str, end_ns: int,
                      post: Optional[int] = None) -> None:
        """Close every span at the close of the recording. Never raises."""
        if self._spans is None:
            return
        try:
            self._finish_spans_strict(status, cooldown_status, end_ns, post)
        except Exception as exc:
            logger.error("recording session %d: closing spans failed: %s",
                         self.session_id, exc)

    def _finish_spans_strict(self, status: str, cooldown_status: str, end_ns: int,
                             post: Optional[int] = None) -> None:
        """Workload, cooling tail, post idle window and session; top level ends at end_ns."""
        sp, ids = self._spans, self._span_ids
        tail_end = post if post is not None else end_ns   # the post window follows the tail
        if "workload" not in ids:                       # no run: all pre-window
            sp.close(ids["pre"], end_ns, self._pre_window_attrs(end_ns))
        else:
            self._close_slots()
            wl_end = self._workload_end
            sp.close(ids["workload"], wl_end, {"runs": self._runs})
            if tail_end > wl_end:
                tail = sp.open("cooling_tail", "cooling_tail", wl_end, ids["session"])
                sp.close(tail, tail_end, {
                    "status": cooldown_status, "reached": cooldown_status == "completed",
                    "seconds": (tail_end - wl_end) / 1e9,
                    "tolerance_c": self.idle.band_c()})
        if post is not None and "workload" in ids:
            pid = sp.open("idle_window", "idle_window:post", post, ids["session"])
            sp.close(pid, end_ns, {"purpose": "post", "planned_s": self.idle.cfg["post_s"],
                                   "actual_s": (end_ns - post) / 1e9})
        sp.close(ids["session"], end_ns, {
            "session_id": int(self.session_id), "profile_id": PROFILE_ID,
            "pacing_plugin": PACING_PLUGIN, "machine_profile": self.machine_profile},
            "closed" if status == "closed" else "error")

    def _close_slots(self) -> None:
        """Close each run slot with the run_id of the one run that starts at its start."""
        for slot, index in self._slots:
            run_id, match = self._match_run(self._spans.start_of(slot))
            if match != "matched":
                logger.warning("recording session %d: run slot %d has no unique run "
                               "(%s)", self.session_id, index, match)
            self._spans.close(slot, None, {"slot_index": index, "run_match": match,
                                           "run_id": run_id})

    def _match_run(self, start_ns: int) -> Tuple[Optional[int], str]:
        """
        run_id of the run whose start_time_ns lies within the tolerance of a
        slot start. run_id is assigned after measurement (EEI-4), so this is
        the only safe link. Returns (run_id or None, matched/none/ambiguous/pending).
        """
        tol = SLOT_MATCH_TOLERANCE_NS
        try:
            w = self.writer_factory()
            w.open()
            try:
                rows = w.execute("SELECT run_id FROM runs WHERE start_time_ns BETWEEN ? AND ?",
                                 (start_ns - tol, start_ns + tol))
            finally:
                w.close()
        except Exception as exc:
            logger.warning("run slot match failed: %s", exc)
            return None, "pending"
        if len(rows) == 1:
            return int(rows[0]["run_id"]), "matched"
        return None, ("none" if not rows else "ambiguous")

    def _flush_spans(self) -> bool:
        """Write dirty spans; False (spans kept) if the write failed. Never raises."""
        try:
            self._flush_spans_strict()
            return True
        except Exception as exc:
            logger.warning("recorder span write failed: %s", exc)
            return False

    def _flush_spans_strict(self) -> None:
        """One batch for every dirty span; on failure they stay dirty."""
        if self._spans is None:
            return
        items = self._spans.drain()
        if not items:
            return
        try:
            w = self.writer_factory()
            w.open()
            try:
                with w.batch():
                    for sql, params in self._spans.statements(items):
                        w.execute(sql, params)
            finally:
                w.close()
        except Exception:
            self._spans.requeue(items)
            raise
        self._spans.mark_written(items)



    def _write_checks(self, end_ns: int) -> None:
        """Rate, gap and clock checks into te_session_check."""
        rows = []
        # ENERGY is read only inside idle windows; idle_session checks its coverage
        for name, st in ((n, s) for n, s in self._stats.items() if n != "ENERGY"):
            # intervals, not reads: n reads span n-1 intervals within each stretch
            achieved = (st.intervals / (st.active_ns / 1e9)) if st.active_ns > 0 else None
            rate_ok = achieved is not None and abs(achieved - st.hz) <= 0.2 * st.hz
            rows.append((self.session_id, "rate_%s" % name.lower(),
                         "pass" if rate_ok else "warn", achieved, st.hz,
                         "ticks=%d emitted=%d errors=%d" % (st.ticks, st.emitted, st.errors),
                         "experiment_recording_v1"))
            gap_limit = 2e9 / st.hz
            rows.append((self.session_id, "gap_%s" % name.lower(),
                         "pass" if st.max_gap_ns <= gap_limit else "fail",
                         st.max_gap_ns / 1e6, gap_limit / 1e6, "ms, between runs only",
                         "experiment_recording_v1"))
        rows.extend(self._clock_checks())
        rows.extend(self._span_checks())
        rows.extend(self.idle.check_rows())
        self._write_rows("INSERT OR REPLACE INTO te_session_check (session_id, check_name, "
                         "result, value, threshold, note, method_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def _span_checks(self) -> List[tuple]:
        """Span structure (no gap, no overlap) and run slot matching, for te_session_check."""
        if self._spans is None:
            return []
        tree = self._spans.tree()
        problems = partition_problems(tree)
        unmatched = sum(1 for r in tree if r["kind"] == "run_slot"
                        and r["attrs"].get("run_match") != "matched")
        return [
            (self.session_id, "spans_partition", "fail" if problems else "pass",
             float(len(problems)), 0.0,
             (problems[0] if problems else "no gap, no overlap")[:200], "recording_spans_v1"),
            (self.session_id, "spans_run_match", "warn" if unmatched else "pass",
             float(unmatched), 0.0, "run slots without exactly one runs row",
             "recording_spans_v1"),
        ]

    def _clock_checks(self) -> List[tuple]:
        """Mapping error (limit 5 ms) and clock steps (warn below one interval)."""
        drifts = [abs(d) for d, _ in self._anchors]
        steps = [s for _, s in self._anchors if s]
        max_drift = max(drifts) / 1e6 if drifts else None
        fastest = max(self.rates.values()) if self.rates else 1.0
        interval_ms = 1000.0 / fastest
        worst_step = max(steps) / 1e6 if steps else 0.0
        step_result = "pass" if not steps else ("warn" if worst_step < interval_ms else "fail")
        return [
            (self.session_id, "clock_mapping_error",
             "pass" if max_drift is not None and max_drift <= 5.0 else "warn",
             max_drift, 5.0, "ms, session time line minus wall clock", CLOCK_BASIS),
            (self.session_id, "clock_step", step_result, worst_step, interval_ms,
             "ms, largest wall-clock step between anchors", CLOCK_BASIS),
        ]


def _retry(fn: Callable[[], Any], what: str, tries: int = 5, raise_last: bool = False) -> Any:
    """Run fn, retrying with backoff while the store is busy; log the final failure."""
    delay = 0.5
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:
            if i == tries - 1:
                logger.error("%s failed after %d attempts: %s", what, tries, exc)
                if raise_last:
                    raise
                return None
            logger.warning("%s failed (attempt %d), retrying: %s", what, i + 1, exc)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)
    return None


def recover_open_sessions(writer_factory: Callable[[], Any], node_name: str) -> int:
    """
    Mark sessions left open by a dead process as aborted (never complete).

    Idempotent: only rows still 'open' whose process no longer exists are
    changed; samples are never touched. Returns the number of rows changed.
    """
    w = writer_factory()
    w.open()
    try:
        rows = w.execute("SELECT session_id, global_session_id FROM te_session "
                         "WHERE status = 'open' AND node_name = ?", (node_name,))
        stale = [r["session_id"] for r in rows if not _pid_alive(r["global_session_id"])]
        if stale:
            with w.batch():
                w.execute_many(
                    "UPDATE te_session SET status = 'aborted', "
                    "status_reason = 'process ended without closing the recording', "
                    "cooldown_status = COALESCE(cooldown_status, 'aborted') "
                    "WHERE session_id = ? AND status = 'open'", [(s,) for s in stale])
                # open spans of these recordings close in the same transaction;
                # only spans still 'open' change, so a second run changes nothing
                w.execute_many(RECOVER_SPANS_SQL, [(s, s, s) for s in stale])
                w.execute_many(RECOVER_PLACEMENTS_SQL, [(s,) for s in stale])
    finally:
        w.close()
    if stale:
        logger.warning("recording recovery: %d open session(s) marked aborted: %s",
                       len(stale), stale)
    return len(stale)


def _pid_alive(global_session_id: str) -> bool:
    """True if the pid encoded in global_session_id ('node-pid-start') still runs."""
    try:
        pid = int(global_session_id.rsplit("-", 2)[-2])
    except (ValueError, IndexError):
        return False
    if pid == os.getpid():
        return global_session_id in _OPEN_IN_PROCESS   # open here: alive; else stale
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # exists but owned by another user
