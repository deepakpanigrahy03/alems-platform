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

from core.execution.outputs.registry import OutputRegistry
from alems_sdk.manifest import ORIGIN_EXTERNAL, ORIGIN_LOCAL, ORIGIN_RUNTIME
from core.registry.loader import load_group
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

output_registry = OutputRegistry()


def _register(cls, config: dict = None) -> None:
    """
    Register one output adapter; failures propagate to the loader.

    The loader records them as refusals at stage register (WP 1a.3 section 5),
    so this bootstrap never interprets a failure itself.
    """
    output_registry.register(cls)


def register_builtin_outputs() -> None:
    """Register runtime output adapters through their entry points (INV-2)."""
    load_group(
        "alems.outputs",
        _register,
        _CORE_VERSION,
        origins=(ORIGIN_RUNTIME,),
    )


def register_external_output_plugins() -> None:
    """Register external and sandbox local output plugins (alems.outputs)."""
    names = load_group(
        "alems.outputs",
        _register,
        _CORE_VERSION,
        origins=(ORIGIN_EXTERNAL, ORIGIN_LOCAL),
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
