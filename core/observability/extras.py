"""
Governed log extras (C-LOG rule 1).

Callers put structured data under one record attribute, alems_extra:
    logger.info("flushed", extra={"alems_extra": {"rows": 12}})
Only keys registered for the logger's component survive; serialized size is
capped. Python logging cannot tell ordinary extra attributes apart, which is
why governed data lives under a single namespaced attribute.
"""

import json
import threading
from typing import Any, Dict, Iterable, Mapping

EXTRA_ATTR = "alems_extra"
MAX_EXTRA_BYTES = 2048

# component prefix -> allowed keys. Prefixes use real logger names (core.*).
_REGISTRY: Dict[str, set] = {}

# Counters are read by tests and, from 2b, written into run provenance.
COUNTERS = {"extra_unknown_dropped": 0, "extra_truncated": 0}
_LOCK = threading.Lock()


def register_extra_keys(component: str, keys: Iterable[str]) -> None:
    """
    Allow keys in extras emitted by loggers under a component prefix.

    Args:
        component: Logger prefix; the alems alias is accepted.
        keys: Key names allowed for that component.
    """
    from core.observability.levels import normalize_component

    prefix = normalize_component(component)
    _REGISTRY.setdefault(prefix, set()).update(keys)


def _allowed(name: str) -> set:
    """Union of keys registered for every prefix of a logger name."""
    out: set = set()
    for prefix, keys in _REGISTRY.items():
        if name == prefix or name.startswith(prefix + "."):
            out |= keys
    return out


def _count(key: str, n: int = 1) -> None:
    """Increment a counter under a lock (handlers may run on many threads)."""
    with _LOCK:
        COUNTERS[key] += n


def govern(name: str, extra: Any) -> Dict[str, Any]:
    """
    Filter and cap an extra mapping.

    Args:
        name: Logger name of the record.
        extra: Raw value of the alems_extra attribute.

    Returns:
        Governed dict; empty when nothing survives.
    """
    if not isinstance(extra, Mapping) or not extra:
        return {}
    allowed = _allowed(name)
    kept = {k: v for k, v in extra.items() if k in allowed}
    dropped = len(extra) - len(kept)
    if dropped:
        _count("extra_unknown_dropped", dropped)
    return _cap(kept)


def _cap(kept: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys from the end until the serialized size fits the cap."""
    keys = list(kept)
    truncated = False
    while keys and _size({k: kept[k] for k in keys}) > MAX_EXTRA_BYTES:
        keys.pop()
        truncated = True
    if truncated:
        _count("extra_truncated")
    return {k: kept[k] for k in keys}


def _size(obj: Dict[str, Any]) -> int:
    """Serialized byte size; default=str so odd values never raise."""
    return len(json.dumps(obj, default=str).encode("utf-8"))
