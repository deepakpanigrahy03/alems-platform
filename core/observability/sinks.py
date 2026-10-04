"""
Sinks: console, host JSON lines file, per run memory buffer, store placeholder.

The per run buffer is how 2a stays window safe before the 2b gate exists
(plan A1): on run entry points nothing is written to disk while the run is
going; the buffer is written to run_<run_uid>.jsonl only when the entry point
calls flush_run_log() at run end, which is after t1 by construction.
"""

import collections
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List

from core.observability.context import FIELDS
from core.observability.extras import EXTRA_ATTR, govern

DEFAULT_RUN_BUFFER = 100000

# Read by tests and, from 2b, recorded in run provenance.
RUN_COUNTERS = {"run_buffer_dropped": 0, "run_records_unkeyed": 0}


def record_to_dict(record: logging.LogRecord) -> Dict[str, Any]:
    """
    Build the C-LOG dict for a record.

    Formatting (getMessage) happens here, at sink time, so the 2b gate can
    defer it until after t1 simply by deferring this call.
    """
    ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
    out = {
        "ts": ts.isoformat(timespec="microseconds"),
        "level": record.levelname,
        "logger": record.name,
        "msg": record.getMessage(),
        "pid": record.process,
        "event_seq": getattr(record, "event_seq", None),
    }
    for name in FIELDS:
        out[name] = getattr(record, name, None)
    out["extra"] = govern(record.name, getattr(record, EXTRA_ATTR, None))
    if record.exc_info:
        out["exc_class"] = record.exc_info[0].__name__ if record.exc_info[0] else None
    return out


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line; default=str so odd values never raise."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record to one line."""
        return json.dumps(record_to_dict(record), default=str)


def console_handler(level: int, mode: str) -> logging.Handler:
    """
    Console handler on stderr.

    quiet and normal print only the message, matching Python's last resort
    handler output that users see today; verbose and debug add level and
    logger name for investigation.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    fmt = "%(message)s"
    if mode in ("verbose", "debug"):
        fmt = "%(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    return handler


def host_file_handler(directory: Path, level: int) -> logging.Handler:
    """
    Host level JSON lines file (alems.jsonl). Rotation arrives in 2h.

    delay=True: the file opens on the first record, so a command that logs
    nothing creates nothing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(directory / "alems.jsonl"), delay=True, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(JsonLinesFormatter())
    return handler


class RunBufferHandler(logging.Handler):
    """
    Bounded, lossy memory buffer of records for per run logs.

    Holds LogRecord objects, not strings: no formatting or serialization
    happens until flush (master 5.2a). Overflow drops the oldest record and
    counts it.
    """

    def __init__(self, level: int, capacity: int = DEFAULT_RUN_BUFFER):
        super().__init__(level)
        self._buf: Deque[logging.LogRecord] = collections.deque(maxlen=capacity)
        self._buf_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Append; never touches disk."""
        with self._buf_lock:
            if len(self._buf) == self._buf.maxlen:
                RUN_COUNTERS["run_buffer_dropped"] += 1
            self._buf.append(record)

    def drain(self) -> List[logging.LogRecord]:
        """Remove and return all buffered records in arrival order."""
        with self._buf_lock:
            items = list(self._buf)
            self._buf.clear()
        return items

    def flush_to(self, directory: Path) -> int:
        """
        Write buffered records to run_<run_uid>.jsonl files.

        Records without run_uid cannot be placed (run_uid arrives in 2c) and
        are counted, then discarded.

        Returns:
            Number of records written.
        """
        groups: Dict[str, List[str]] = {}
        for record in self.drain():
            uid = getattr(record, "run_uid", None)
            if not uid:
                RUN_COUNTERS["run_records_unkeyed"] += 1
                continue
            line = json.dumps(record_to_dict(record), default=str)
            groups.setdefault(str(uid), []).append(line)
        if groups:
            directory.mkdir(parents=True, exist_ok=True)
        for uid, lines in groups.items():
            _append_lines(directory / ("run_%s.jsonl" % uid), lines)
        return sum(len(v) for v in groups.values())


def _append_lines(path: Path, lines: List[str]) -> None:
    """Append lines and fsync so a flushed run log survives a crash."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class StoreSinkPlaceholder(logging.Handler):
    """Store sink (stage events plus WARN and above); enabled in 2c."""

    enabled = False

    def emit(self, record: logging.LogRecord) -> None:
        """No op until 2c wires the writer."""
        return None
