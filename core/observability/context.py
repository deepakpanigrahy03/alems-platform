"""
Log context (C-LOG fields) carried through contextvars.

Context is immutable per scope: bind() pushes a new mapping and restores the
outer one on exit, so nested scopes (run inside experiment, stage inside run)
never leak fields outward. contextvars gives each thread and asyncio task its
own view, which is what we want for concurrent sessions in one process.
"""

import contextvars
import logging
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, Dict, Iterator, Mapping

# The C-LOG hierarchy fields (master 5.2). Order is the grouping hierarchy.
FIELDS = (
    "host",
    "engine_version",
    "sandbox_id",
    "exp_id",
    "run_uid",
    "run_id",
    "stage_id",
    "span_id",
)

# Process wide base values (host, engine_version) set once at setup.
_BASE: Dict[str, Any] = {}

# Scoped values. Default is an empty read only mapping so a stray mutation
# attempt fails loudly instead of corrupting other scopes.
_CTX: "contextvars.ContextVar[Mapping[str, Any]]" = contextvars.ContextVar(
    "alems_obs_ctx", default=MappingProxyType({})
)


def _check_keys(fields: Mapping[str, Any]) -> None:
    """
    Reject unknown context keys.

    Args:
        fields: Proposed context values.

    Raises:
        KeyError: a key is not a C-LOG field. This is a programming error,
            caught in tests, so failing loudly is correct here.
    """
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise KeyError("unknown log context fields: %s" % sorted(unknown))


def set_base_context(**fields: Any) -> None:
    """
    Set process wide fields (normally host and engine_version).

    Args:
        **fields: C-LOG field values valid for the whole process.
    """
    _check_keys(fields)
    _BASE.update(fields)


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """
    Bind context fields for the duration of a with block.

    Args:
        **fields: C-LOG field values; None clears a field in this scope.

    Yields:
        Nothing; records logged inside the block carry the fields.
    """
    _check_keys(fields)
    merged = dict(_CTX.get())
    merged.update(fields)
    token = _CTX.set(MappingProxyType(merged))
    try:
        yield
    finally:
        # reset, not set: restores exactly the outer scope even if an inner
        # scope rebound the same keys.
        _CTX.reset(token)


def get_context() -> Dict[str, Any]:
    """
    Return the effective context: base values overlaid by scoped values.

    Returns:
        Dict with every C-LOG field; missing fields are None.
    """
    out = {f: _BASE.get(f) for f in FIELDS}
    out.update({k: v for k, v in _CTX.get().items()})
    return out


class ContextFilter(logging.Filter):
    """Copy the effective context onto each record as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Attach context fields; never rejects a record.

        Args:
            record: Record being handled.

        Returns:
            Always True (this filter annotates, it does not select).
        """
        ctx = get_context()
        for name in FIELDS:
            # A caller may set a field explicitly on the record; keep it.
            if not hasattr(record, name):
                setattr(record, name, ctx[name])
        return True
