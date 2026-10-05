"""
Logging setup: one idempotent entry used by every entry point.

Two entry kinds (plan A1):
  cli  console plus host JSON lines file.
  run  console plus per run memory buffer; no file I/O until flush_run_log()
       at run end. The host file joins run entry points in 2b behind the
       window gate.
Only handlers created here are ever removed; foreign handlers are left alone.
"""

import logging
import socket
import sys
from typing import Any, Dict, List, Mapping, Optional
from core.observability import gate

from core.observability import levels, locations, sinks
from core.observability.context import ContextFilter, set_base_context

# Marker attribute on handlers we own, so reconfiguration never touches
# handlers installed by third parties or by tests.
_OWNED = "_alems_obs_owned"

_STATE: Dict[str, Any] = {"key": None, "config": None, "run_buffer": None}


def _engine_version() -> Optional[str]:
    """Installed runtime version, or None (no version constant exists yet)."""
    try:
        from importlib.metadata import version

        return version("alems")
    except Exception:  # noqa: BLE001
        return None


def _own(handler: logging.Handler) -> logging.Handler:
    """Mark a handler as ours and attach the context filter."""
    setattr(handler, _OWNED, True)
    handler.addFilter(ContextFilter())
    return handler


def _remove_owned(root: logging.Logger) -> None:
    """Detach and close handlers we installed earlier."""
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()


def _build_handlers(entry: str, cfg: levels.LogConfig) -> List[logging.Handler]:
    """Create the handler set for an entry kind."""
    out = [_own(sinks.console_handler(cfg.console_level, cfg.mode))]
    # Component overrides apply to the full record sinks, not the console.
    comp_filter = levels.ComponentFilter(cfg.file_level, cfg.components)
    file_floor = min([cfg.file_level] + list(cfg.components.values()))
    if entry == "run":
        buf = sinks.RunBufferHandler(file_floor)
        buf.addFilter(comp_filter)
        _STATE["run_buffer"] = buf
        out.append(_own(buf))
        return out
    directory = _safe(locations.host_log_dir)
    if directory is not None:
        handler = sinks.host_file_handler(directory, file_floor)
        handler.addFilter(comp_filter)
        out.append(_own(handler))
    return out


def _safe(fn: Any) -> Any:
    """Call a location function; on failure warn on stderr and return None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001  observability is subordinate
        sys.stderr.write("alems: log location unavailable: %s\n" % exc)
        return None


def setup_logging(
    entry: str = "cli",
    cli: Optional[Mapping[str, object]] = None,
    layers: Optional[List[Optional[Mapping[str, object]]]] = None,
) -> levels.LogConfig:
    """
    Configure logging once per process; repeat calls with the same inputs are no ops.

    Args:
        entry: "cli" or "run".
        cli: Command line layer (mode, level, components); highest precedence.
        layers: Machine, sandbox, profile layers, lowest first.

    Returns:
        The effective LogConfig.
    """
    if entry not in ("cli", "run"):
        raise ValueError("entry must be cli or run: %r" % entry)
    # No silent fallback (39.5.2d): a run needs host log and error directories
    # and stops here before any measurement; the CLI must still reach
    # doctor and help, so it reports the same fix on stderr instead.
    from core.observability import locations as _loc
    if entry == "run":
        _loc.require_host_dirs()
    elif not _loc.host_dirs_configured():
        sys.stderr.write(_loc.missing_data_root_message() + "\n")
    ordered = list(layers or []) + [levels.env_layer(), dict(cli or {})]
    try:
        cfg = levels.resolve_config(ordered)
    except ValueError as exc:
        if entry == "cli":
            raise  # the CLI maps this to a usage error (exit 2)
        # Observability is subordinate: a bad logging setting never stops a run.
        sys.stderr.write("alems: invalid logging setting ignored: %s\n" % exc)
        cfg = levels.resolve_config([])
    key = (entry, levels.config_hash(cfg))
    if _STATE["key"] == key:
        return _STATE["config"]
    root = logging.getLogger()
    _remove_owned(root)
    set_base_context(host=socket.gethostname().lower(), engine_version=_engine_version())
    gate.install_record_factory()  # event_seq on every record (master 5.1 rule 4)
    for handler in _build_handlers(entry, cfg):
        # I/O sinks are held by the gate inside the window; the per run buffer
        # is memory only and stays direct.
        if not isinstance(handler, sinks.RunBufferHandler):
            handler = _own(gate.wrap(handler))
        root.addHandler(handler)
    gate.set_hash_provider(lambda: levels.config_hash(_STATE["config"]))
    # Root passes only what some sink needs, so disabled debug calls stay cheap.
    root.setLevel(cfg.lowest_level())
    _STATE.update(key=key, config=cfg)
    return cfg


def flush_run_log(store_path: Optional[str] = None) -> int:
    """
    Write the per run buffer beside the store. Call at run end only.

    Args:
        store_path: Store path; resolved through resolve_store() when None.

    Returns:
        Records written; 0 when not in run mode or on any failure.
    """
    gate.force_exit()  # a window left open by a task exception is closed here
    buf = _STATE.get("run_buffer")
    if buf is None:
        return 0
    try:
        if store_path is None:
            from core.storage.resolver import resolve_store

            store_path = resolve_store()
        return buf.flush_to(locations.run_log_dir(store_path))
    except Exception as exc:  # noqa: BLE001  never fail the run for logging
        sys.stderr.write("alems: per run log flush failed: %s\n" % exc)
        return 0


def shutdown_logging() -> None:
    """Remove our handlers and reset state (tests and process exit)."""
    gate.force_exit()
    gate.set_hash_provider(None)
    _remove_owned(logging.getLogger())
    _STATE.update(key=None, config=None, run_buffer=None)
