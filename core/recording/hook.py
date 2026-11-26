"""
Run boundary hook for continuous experiment recording.

The harness calls notify_run_start() just before a run's measurement starts
and notify_run_end() just after it stops. When a recording is open its
recorder pauses for the run (the run's own samplers own those samples) and
resumes after it. When no recording is open both calls do nothing, so normal
runs behave exactly as before.

The hook never raises: a recorder problem must not break a run.
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active: Optional[Any] = None     # object with pause() and resume()


def set_active(recorder: Any) -> None:
    """Register the open recording (one per process)."""
    global _active
    with _lock:
        if _active is not None and _active is not recorder:
            logger.warning("recording hook: replacing an active recorder")
        _active = recorder


def clear_active(recorder: Optional[Any] = None) -> None:
    """Unregister; with an argument, only if it is the active recorder."""
    global _active
    with _lock:
        if recorder is None or _active is recorder:
            _active = None


def get_active() -> Optional[Any]:
    """The open recorder, or None."""
    return _active


def notify_run_start() -> None:
    """A run's measurement is about to start: pause the recording."""
    _safety_boundary(True)   # the safety guard tracks active work (never raises)
    rec = _active
    if rec is None:
        return
    try:
        rec.pause()
    except Exception as exc:          # never let recording break a run
        logger.warning("recording hook: pause failed: %s", exc)


def notify_run_end() -> None:
    """A run's measurement has stopped: resume the recording."""
    _safety_boundary(False)  # the safety guard tracks active work (never raises)
    rec = _active
    if rec is None:
        return
    try:
        rec.resume()
    except Exception as exc:
        logger.warning("recording hook: resume failed: %s", exc)


def _safety_boundary(started: bool) -> None:
    """Tell the safety guard that a run started or ended; never raises."""
    try:
        from core.safety import hook as _safety
        (_safety.workload_started if started else _safety.workload_ended)()
    except Exception as exc:
        logger.warning("recording hook: safety notice failed: %s", exc)
