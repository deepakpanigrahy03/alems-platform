#!/usr/bin/env python3
"""
================================================================================
SELECTOR BOOTSTRAP  —  core/execution/tools/selector_bootstrap.py
================================================================================
Registers the builtin static selector and discovers external selector
plugins (e.g. alems-selector-retrieval, which also registers as an
extension — see CR-4) via entry_points(group="alems.tool_selectors").

AUTHOR: Deepak Panigrahy
SPEC:   35H Part 3
================================================================================
"""

import logging

from core.execution.tools.selector_registry import ToolSelectorRegistry
from alems_sdk.manifest import (
    ORIGIN_EXTERNAL, ORIGIN_LOCAL, PluginUnavailable,
)
from core.registry.loader import load_group
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

selector_registry = ToolSelectorRegistry()


def _register(cls, config: dict = None) -> None:
    """
    Register one selector if its capability check passes (family policy).

    Raises PluginUnavailable when an optional dependency is missing, so the
    loader lists it as unavailable instead of counting it as registered.
    """
    # Same construction as before 1a.3: is_available() only tries an import.
    if not cls().is_available():
        raise PluginUnavailable(
            "%s: capability check failed (optional dependency missing)" % cls.__name__
        )
    selector_registry.register(cls)


def register_external_selector_plugins() -> None:
    """Register external and sandbox local selectors only."""
    names = load_group(
        "alems.tool_selectors", _register, _CORE_VERSION,
        origins=(ORIGIN_EXTERNAL, ORIGIN_LOCAL),
    )
    if names:
        logger.info(
            "selector_bootstrap: %d external selector(s) registered via "
            "entry_points: %s", len(names), names,
        )


def register_all_selectors() -> None:
    """Register every selector through the single plugin path. Idempotent."""
    if not selector_registry.is_empty():
        logger.debug("selector_bootstrap: already registered — skipping")
        return
    logger.info("selector_bootstrap: registering selectors (SPEC 35H)")
    load_group("alems.tool_selectors", _register, _CORE_VERSION)
    logger.info(
        "selector_bootstrap: registered %d selector(s): %s",
        len(selector_registry.get_all()), list(selector_registry.get_all().keys()),
    )