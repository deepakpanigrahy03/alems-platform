#!/usr/bin/env python3
"""
================================================================================
OUTPUT BOOTSTRAP  —  core/execution/outputs/bootstrap.py
================================================================================

PURPOSE:
    Register the builtin CSV/JSON output adapters and discover
    external output plugins via entry_points(group="alems.outputs").

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 7
================================================================================
"""

import logging

from core.execution.outputs.registry import OutputRegistry, DuplicateOutputError
from core.plugin_discovery import discover_plugins
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

output_registry = OutputRegistry()


def _safe_register(cls, config: dict = None) -> None:
    try:
        output_registry.register(cls)
    except DuplicateOutputError:
        raise
    except Exception as exc:
        logger.warning(
            "output_bootstrap: failed to register %s: %s — skipping",
            getattr(cls, "__name__", repr(cls)), exc,
        )


def register_builtin_outputs() -> None:
    """Register CSV and JSON adapters. Import isolation per family convention."""
    try:
        from core.execution.outputs.csv_output import CSVOutputAdapter
        _safe_register(CSVOutputAdapter)
    except ImportError as exc:
        logger.debug("output_bootstrap: CSVOutputAdapter not importable: %s", exc)

    try:
        from core.execution.outputs.json_output import JSONOutputAdapter
        _safe_register(JSONOutputAdapter)
    except ImportError as exc:
        logger.debug("output_bootstrap: JSONOutputAdapter not importable: %s", exc)


def register_external_output_plugins() -> None:
    """Discover external output plugins via entry_points(group="alems.outputs")."""
    names = discover_plugins(
        group="alems.outputs",
        register_fn=lambda cls, cfg: _safe_register(cls, cfg),
        core_version=_CORE_VERSION,
    )
    if names:
        logger.info(
            "output_bootstrap: %d external output adapter(s) registered via "
            "entry_points: %s", len(names), names,
        )


def register_all_outputs() -> None:
    """Register builtins, then discover external plugins. Idempotent."""
    if not output_registry.is_empty():
        logger.debug("output_bootstrap: already registered — skipping")
        return
    logger.info("output_bootstrap: registering builtin output adapters (SPEC 35G)")
    register_builtin_outputs()
    register_external_output_plugins()
    logger.info(
        "output_bootstrap: registered %d output adapter(s): %s",
        len(output_registry.get_all()), list(output_registry.get_all().keys()),
    )
