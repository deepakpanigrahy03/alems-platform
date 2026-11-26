"""
core/safety/hook.py: the process's safety guard, opened once and used by every
run path.

before_run(kind) is called at the very top of ExperimentHarness.run_linear and
run_agentic, before anything of the run happens, so save_pair, save_single,
every retry attempt and sequencer steps all pass the same gate. It opens the
guard on first use (settings safety.thermal in config/app_settings.yaml), then
waits for admission; the wait is unbounded and the guard never aborts a run.
Only a refusal at start (missing or invalid safety.thermal, no headroom source,
an invalid idle reference) raises GuardRefused and halts the process: every
later call raises at once.

workload_started() / workload_ended() are called by the run boundary hook
(core.recording.hook) around each measurement window; they never raise.
With safety.thermal.enabled false nothing is opened and every call returns.
A missing or invalid safety.thermal block fails closed (refusal).
"""

import atexit
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.safety.thermal_guard import GuardRefused

logger = logging.getLogger(__name__)

_SETTINGS = Path(__file__).resolve().parents[2] / "config" / "app_settings.yaml"
_lock = threading.RLock()
_guard = None          # type: Optional[Any]
_halt = None           # type: Optional[GuardRefused]
_disabled = False
_factory = None        # type: Optional[Callable[[Dict[str, Any], str], Any]]
_atexit_done = False


def load_settings():
    # type: () -> Optional[Dict[str, Any]]
    """The safety.thermal block, or None when absent."""
    import yaml
    with open(str(_SETTINGS), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return (doc.get("safety") or {}).get("thermal")


def default_factory(cfg, entry_path):
    # type: (Dict[str, Any], str) -> Any
    """Guard on the resolved store: NVML margins and zone critical trips, host log first, store via the writer."""
    from core.readers.nvml_signals import NvmlSignals
    from core.safety.store_sink import StoreSink, host_log_path
    from core.safety.thermal_guard import JsonlEventLog, NvmlMarginSource, ThermalGuard, ZoneTripSource
    from core.thermal.thermal_descriptors import list_zones, read_zone_c, zone_trip_points
    from core.storage.inprocess_writer import InProcessWriter
    from core.storage.resolver import resolve_store
    store = resolve_store()
    sink = StoreSink(lambda: InProcessWriter(store))
    sources = [NvmlMarginSource(NvmlSignals()), ZoneTripSource(list_zones, zone_trip_points, read_zone_c)]
    guard = ThermalGuard(cfg, sources, JsonlEventLog(host_log_path(store)), sink, entry_path=entry_path)
    sink.attach(guard)
    return guard


def set_factory(fn):
    # type: (Optional[Callable[[Dict[str, Any], str], Any]]) -> None
    """Tests: build guards with fakes."""
    global _factory
    _factory = fn


def reset():
    # type: () -> None
    """Tests: close and forget the process guard and any halt."""
    global _guard, _halt, _disabled
    with _lock:
        _close_quietly(_guard, "reset")
        _guard, _halt, _disabled = None, None, False


def _close_quietly(guard, reason):
    # type: (Optional[Any], str) -> None
    """Close a guard for a reset; a close error is logged, never raised."""
    if guard is None:
        return
    try:
        guard.close(reason)
    except Exception as exc:              # reset is for tests; a close error must still be visible
        logger.warning("safety guard: close during reset failed: %s", exc)


def halted():
    # type: () -> Optional[str]
    """Reason code when no further work may start in this process, else None."""
    if _halt is not None:
        return _halt.code
    return None


def _raise_if_halted():
    # type: () -> None
    """Raise the stored refusal."""
    if _halt is not None:
        raise _halt


def _halt_with(exc):
    # type: (GuardRefused) -> None
    """Remember the first refusal: later runs in this process are refused at once."""
    global _halt
    if _halt is None:
        _halt = exc
        logger.error("safety guard: no further work in this process (%s)", exc)


def ensure_guard(entry_path="harness"):
    # type: (str) -> Optional[Any]
    """The open guard (opened now if needed); None when disabled by config."""
    global _guard, _disabled, _atexit_done
    with _lock:
        _raise_if_halted()
        if _guard is not None or _disabled:
            return _guard
        try:
            cfg = load_settings()
        except Exception as exc:
            err = GuardRefused("config_unreadable", str(exc))
            _halt_with(err)
            raise err
        if cfg is None:
            err = GuardRefused("config_missing", "safety.thermal block missing in config/app_settings.yaml")
            _halt_with(err)
            raise err
        if cfg.get("enabled") is False:
            _disabled = True
            logger.warning("safety guard disabled by safety.thermal.enabled")
            return None
        try:
            guard = (_factory or default_factory)(cfg, entry_path)
            guard.open()
        except GuardRefused as exc:
            _halt_with(exc)
            raise
        except Exception as exc:          # invalid config, reader failure: fail closed
            err = GuardRefused("guard_unavailable", str(exc))
            _halt_with(err)
            raise err
        guard.start_watchdog()
        _guard = guard
        if not _atexit_done:
            atexit.register(close, "process_exit")
            _atexit_done = True
        logger.info("safety guard open (episode %s, entry %s)", guard.episode["episode_id"], entry_path)
        return guard


def before_run(kind, links=None):
    # type: (str, Optional[Dict[str, Any]]) -> None
    """Admission before a run: waits until the hardware is cool; raises GuardRefused only for a refusal."""
    guard = ensure_guard()
    if guard is None:
        return
    if guard.active_count():
        guard.workload_ended()            # previous run ended without its end notice (error path)
    _flush_store(guard)                   # between runs, on this (the main) thread
    try:
        guard.admit(links=dict(links or {}, kind=kind))
    except GuardRefused as exc:
        _halt_with(exc)
        raise
    finally:
        _flush_store(guard)               # records of the wait, before the run starts


def _flush_store(guard):
    # type: (Any) -> None
    """Write queued guard records now; a failure keeps them queued (never raised into a run)."""
    sink = getattr(guard, "_store", None)
    if sink is not None and hasattr(sink, "flush"):
        sink.flush()


def workload_started():
    # type: () -> None
    """Run boundary notice: a measurement window opened (never raises into the run)."""
    guard = _guard
    if guard is not None:
        guard.workload_started()


def workload_ended():
    # type: () -> None
    """Run boundary notice: a measurement window closed; the rest timer starts here."""
    guard = _guard
    if guard is not None:
        guard.workload_ended()


def close(reason="completed"):
    # type: (str) -> None
    """End the episode and write what is still buffered."""
    with _lock:
        guard = _guard
    if guard is None:
        return
    try:
        guard.close(reason)
    finally:
        _close_sink(guard)               # buffered records are written even if close failed


def _close_sink(guard):
    # type: (Any) -> None
    """Write what the guard's store sink still holds."""
    sink = getattr(guard, "_store", None)
    if sink is not None and hasattr(sink, "close"):
        sink.close()
