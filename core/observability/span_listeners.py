"""
core/observability/span_listeners.py: forward span open and close events to
listener plugins (entry point group alems.spans.listeners).

Why: tools outside A-LEMS (profilers, tracers) can show A-LEMS phases on their
own timelines without core knowing them. Hardware independent: with no
listener installed or enabled, notify calls return immediately.
Listeners are loaded once per process; a listener that raises is logged and
disabled for the rest of the process, so it can never disturb a measurement.
"""

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
GROUP = "alems.spans.listeners"
_LISTENERS = None  # type: Any


def _load():
    # type: () -> List[Any]
    """Enabled listener instances from the entry point group."""
    out = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group=GROUP) if hasattr(eps, "select") else eps.get(GROUP, [])
    except Exception as exc:
        logger.warning("span listeners: discovery failed: %s", exc)
        return out
    settings = _plugin_settings()
    for ep in group:
        try:
            cls = ep.load()
            listener = cls()
            key = getattr(cls, "CONFIG_KEY", "") or ep.name
            if hasattr(listener, "configure"):
                listener.configure(resolve_config(key, settings.get(key) or {}))
            if listener.enabled():
                out.append(listener)
                logger.info("span listener %s enabled", ep.name)
        except Exception as exc:
            logger.warning("span listener %s not loadable: %s", ep.name, exc)
    return out


def _plugin_settings():
    # type: () -> Dict[str, Any]
    """plugins: section of app_settings.yaml; empty when unreadable (listeners then stay off)."""
    try:
        from core.config_loader import ConfigLoader
        return dict(ConfigLoader().get_settings().get("plugins") or {})
    except Exception as exc:
        logger.warning("span listeners: app_settings unreadable: %s", exc)
        return {}


def resolve_config(key, base, env=None):
    # type: (str, Dict[str, Any], Any) -> Dict[str, Any]
    """
    Plugin settings: app_settings plugins.<key>, then ALEMS_<KEY>_<FIELD> from
    ~/.alemsrc or the shell for each field the section declares (env wins).
    Booleans accept 1/0/true/false.
    """
    env = os.environ if env is None else env
    out = dict(base)
    for field, value in base.items():
        raw = env.get("ALEMS_%s_%s" % (key.upper(), field.upper()))
        if raw is None or raw == "":
            continue
        if isinstance(value, bool):
            out[field] = raw.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[field] = type(value)(raw)
        else:
            out[field] = raw
    return out


def listeners():
    # type: () -> List[Any]
    global _LISTENERS
    if _LISTENERS is None:
        _LISTENERS = _load()
    return _LISTENERS


def _as_dict(record):
    # type: (Any) -> Dict[str, Any]
    """Plain span dict for listeners (no core object leaks to plugins)."""
    return {k: getattr(record, k, None) for k in
            ("span_id", "trace_id", "parent_span_id", "kind", "name", "start_ns", "end_ns", "status")}


def _notify(method, record):
    # type: (str, Any) -> None
    active = listeners()
    if not active:
        return
    span = _as_dict(record)
    for listener in list(active):
        try:
            getattr(listener, method)(span)
        except Exception as exc:   # disabled for the rest of the process, never raised
            logger.warning("span listener %s disabled after error: %s", type(listener).__name__, exc)
            active.remove(listener)


def on_open(record):
    _notify("on_open", record)


def on_close(record):
    _notify("on_close", record)
