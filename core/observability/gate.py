"""
Measurement window gate (39.5.2 WP 2b, master 5.2a, design 39.5.2 section 14).

One process global gate. While it is closed (inside), observability performs
no filesystem, store, or network I/O and no formatting: log records bound for
I/O sinks are held as LogRecord objects, stage events, errors, and provenance
records are held in non lossy buffers. At exit everything is released in
event_seq order.

Placement (amended SPEC_39_5_2 section 4): the harness calls enter() just
before reset_interrupt_samples and exit() just after the duration
computations, so the complete SPEC_39_4 section 3a sequence sits inside and
nothing is inserted between its steps. The gated interval is a superset of
[t0, t1].

Observability is subordinate to the application: no function here raises.
"""

import collections
import itertools
import logging
import os
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

_log = logging.getLogger("alems.observability.gate")

# Capacities: logs are lossy (oldest dropped), the rest refuse and flag.
LOG_CAPACITY = 50000
NON_LOSSY_CAPACITY = 10000
NON_LOSSY_KINDS = ("event", "error", "provenance")

# Optional file the strace check reads; written only at exit (outside).
TRACE_ENV = "ALEMS_GATE_TRACE"

_lock = threading.Lock()
# itertools.count.__next__ is atomic under the GIL; one counter per process
# gives every record kind a shared order (master 5.1 rule 4).
_seq = itertools.count(1)

_state: Dict[str, Any] = {
    "inside": False,
    "overflow": False,       # reset at every enter; one window at a time
    "enter_wall": None,
    "enter_perf": None,
    "level_name": None,
    "srcfile": None,         # saved logging._srcfile while inside
    "held": 0,               # log records held in the current window (overhead bound)
    "created": 0,            # every log record created in the current window, any sink
}
_logs: Deque[Tuple[logging.Handler, logging.LogRecord]] = collections.deque()
_held: Dict[str, List[Dict[str, Any]]] = {k: [] for k in NON_LOSSY_KINDS}
_consumers: Dict[str, List[Callable[[List[Dict[str, Any]]], None]]] = {
    k: [] for k in NON_LOSSY_KINDS
}
COUNTERS: Dict[str, int] = {
    "gate_log_dropped": 0,
    "gate_nonlossy_refused": 0,
    "gate_forced_exit": 0,
    "gate_consumer_failed": 0,
    "gate_flush_failed": 0,
}
# Set by setup_logging: returns the config hash of the effective logging config.
_hash_provider: Optional[Callable[[], Optional[str]]] = None


def next_seq() -> int:
    """Return the next event_seq of this process."""
    return next(_seq)


def is_inside() -> bool:
    """True while the gate is closed (a plain bool read, no lock)."""
    return bool(_state["inside"])


def set_hash_provider(fn: Optional[Callable[[], Optional[str]]]) -> None:
    """Register the callable that returns measurement_log_config_hash."""
    global _hash_provider
    _hash_provider = fn


def register_consumer(kind: str, fn: Callable[[List[Dict[str, Any]]], None]) -> None:
    """
    Register a consumer for a non lossy kind (2c stage events, 2d errors).

    Consumers receive the held records once, in event_seq order, at exit,
    and receive records directly when submitted outside the gate.
    """
    if kind in _consumers and fn not in _consumers[kind]:
        _consumers[kind].append(fn)


def install_record_factory() -> None:
    """
    Give every LogRecord an event_seq at creation (any thread, any logger).

    Idempotent. A record factory is the only hook that sees every record,
    including those that reach no wrapped handler.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, "_alems_seq", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        record.event_seq = next(_seq)
        if _state["inside"]:
            # Unlocked increment: an approximate count is enough for the bound,
            # and a lock here would add cost to every in window record.
            _state["created"] += 1
        return record

    factory._alems_seq = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


class GatedHandler(logging.Handler):
    """
    Wrapper around an I/O sink (console, host file).

    Filters and the level check of the target run at record time, so context
    fields are captured in the emitting thread; only the target's emit, which
    formats and writes, is deferred while the gate is closed.
    """

    def __init__(self, target: logging.Handler):
        super().__init__(target.level)
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        """Hold the record while inside; otherwise write through."""
        if record.levelno < self.target.level or not self.target.filter(record):
            return
        if not _state["inside"]:
            _write(self.target, record)
            return
        _hold_log(self.target, record)

    def close(self) -> None:
        """Close the target too."""
        self.target.close()
        super().close()


def wrap(handler: logging.Handler) -> logging.Handler:
    """Return handler wrapped for the gate (I/O sinks only)."""
    return GatedHandler(handler)


def _write(target: logging.Handler, record: logging.LogRecord) -> None:
    """Emit through the target under its lock; logging handles its own errors."""
    target.acquire()
    try:
        target.emit(record)
    finally:
        target.release()


def _hold_log(target: logging.Handler, record: logging.LogRecord) -> None:
    """Append a held log record; drop the oldest when full."""
    with _lock:
        if len(_logs) >= LOG_CAPACITY:
            _logs.popleft()
            COUNTERS["gate_log_dropped"] += 1
        _logs.append((target, record))
        _state["held"] += 1


def submit(kind: str, record: Dict[str, Any]) -> bool:
    """
    Submit a stage event, error, or provenance record.

    The record gets event_seq if absent. Inside: held (non lossy; a full
    buffer refuses and flags overflow). Outside: delivered to consumers now.

    Returns:
        False only when the record was refused for overflow.
    """
    if kind not in _held:
        return False
    record.setdefault("event_seq", next(_seq))
    if not _state["inside"]:
        _deliver(kind, [record])
        return True
    with _lock:
        if len(_held[kind]) >= NON_LOSSY_CAPACITY:
            _state["overflow"] = True
            COUNTERS["gate_nonlossy_refused"] += 1
            return False
        _held[kind].append(record)
    return True


def _deliver(kind: str, records: List[Dict[str, Any]]) -> None:
    """Hand records to consumers; a failing consumer is counted, never raised."""
    for fn in list(_consumers[kind]):
        try:
            fn(records)
        except Exception:  # noqa: BLE001  observability is subordinate
            COUNTERS["gate_consumer_failed"] += 1


def enter() -> None:
    """
    Close the gate. Called once per window, before the 3a sequence.

    A gate left closed by an earlier window (task exception skipped exit) is
    force opened first and counted.
    """
    try:
        if _state["inside"]:
            force_exit()
        root = logging.getLogger()
        _state["level_name"] = logging.getLevelName(root.getEffectiveLevel())
        _state["overflow"] = False
        _state["held"] = 0
        _state["created"] = 0
        _state["enter_wall"] = time.time()
        _state["enter_perf"] = time.perf_counter()
        # Caller lookup runs inside Logger before any handler, so the only way
        # to keep stack inspection out of the window is to switch it off for
        # the process (D5). The window is process wide, so this scope is exact.
        _state["srcfile"] = logging._srcfile  # type: ignore[attr-defined]
        logging._srcfile = None  # type: ignore[attr-defined]
        _state["inside"] = True
    except Exception:  # noqa: BLE001
        _state["inside"] = False


def exit() -> Dict[str, Any]:  # noqa: A001  pairs with enter()
    """
    Open the gate, release held records in event_seq order, and return
    the run provenance fields of this window.

    Returns:
        measurement_log_level, measurement_log_config_hash,
        observability_overflow (0 or 1); values are None when the gate never
        engaged (stored as NULL: not recorded).
    """
    result = {
        "measurement_log_level": None,
        "measurement_log_config_hash": None,
        "observability_overflow": None,
    }
    if not _state["inside"]:
        return result
    exit_wall = time.time()
    _open()
    result["measurement_log_level"] = _state["level_name"]
    result["measurement_log_config_hash"] = _config_hash()
    result["observability_overflow"] = 1 if _state["overflow"] else 0
    _release()
    _trace(exit_wall)
    return result


def _open() -> None:
    """Restore caller lookup and mark the gate open."""
    logging._srcfile = _state["srcfile"]  # type: ignore[attr-defined]
    _state["inside"] = False


def _config_hash() -> Optional[str]:
    """Hash of the effective logging config, None if logging is not set up."""
    if _hash_provider is None:
        return None
    try:
        return _hash_provider()
    except Exception:  # noqa: BLE001
        return None


def _release() -> None:
    """Write held logs in event_seq order, then deliver non lossy kinds."""
    with _lock:
        logs = sorted(_logs, key=lambda item: getattr(item[1], "event_seq", 0))
        _logs.clear()
        held = {k: sorted(v, key=lambda r: r["event_seq"]) for k, v in _held.items()}
        for v in _held.values():
            v.clear()
    for target, record in logs:
        try:
            _write(target, record)
        except Exception:  # noqa: BLE001
            COUNTERS["gate_flush_failed"] += 1
    for kind, records in held.items():
        if records:
            _deliver(kind, records)
    if _state["overflow"]:
        _log.warning(
            "observability buffer overflow in measurement window: %d records refused; "
            "run is not paper eligible", COUNTERS["gate_nonlossy_refused"],
        )


def _trace(exit_wall: float) -> None:
    """Append the window interval for the strace check (opt in, outside)."""
    path = os.environ.get(TRACE_ENV)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%d %.6f %.6f %d %d\n" % (os.getpid(), _state["enter_wall"], exit_wall,
                                               _state["held"], _state["created"]))
    except OSError:
        pass  # the check reports a missing interval; the run is unaffected


def force_exit() -> bool:
    """
    Open a gate left closed (safety net for flush_run_log, shutdown, enter).

    Returns:
        True when the gate was closed and has been opened.
    """
    if not _state["inside"]:
        return False
    COUNTERS["gate_forced_exit"] += 1
    try:
        _open()
        _release()
    except Exception:  # noqa: BLE001
        _state["inside"] = False
    return True


def counters() -> Dict[str, int]:
    """Copy of the gate counters."""
    with _lock:
        return dict(COUNTERS)


def reset_for_tests() -> None:
    """Reset state, buffers, consumers, and counters (tests only)."""
    force_exit()
    with _lock:
        _logs.clear()
        for k in NON_LOSSY_KINDS:
            _held[k].clear()
            _consumers[k].clear()
        for k in COUNTERS:
            COUNTERS[k] = 0
    _state["overflow"] = False
