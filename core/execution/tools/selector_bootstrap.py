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

from core.execution.tools.selector_registry import (
    ToolSelectorRegistry, DuplicateToolSelectorError,
)
from core.plugin_discovery import discover_plugins
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

selector_registry = ToolSelectorRegistry()


def _safe_register(cls, config: dict = None) -> None:
    try:
        selector_registry.register(cls)
    except DuplicateToolSelectorError:
        raise
    except Exception as exc:
        logger.warning(
            "selector_bootstrap: failed to register %s: %s — skipping",
            getattr(cls, "__name__", repr(cls)), exc,
        )


def register_external_selector_plugins() -> None:
    names = discover_plugins(
        group="alems.tool_selectors",
        register_fn=lambda cls, cfg: _safe_register(cls, cfg),
        core_version=_CORE_VERSION,
    )
    if names:
        logger.info(
            "selector_bootstrap: %d external selector(s) registered via "
            "entry_points: %s", len(names), names,
        )


def register_all_selectors() -> None:
    if not selector_registry.is_empty():
        logger.debug("selector_bootstrap: already registered — skipping")
        return
    logger.info("selector_bootstrap: registering builtin static selector (SPEC 35H)")
    from core.execution.tools.static_selector import StaticToolSelector
    _safe_register(StaticToolSelector)

    # SPEC 35I: retrieval selector, optional — only registered if
    # sentence-transformers is actually installed (requirements-selectors.txt).
    try:
        from core.execution.tools.retrieval_selector import RetrievalToolSelector
        if RetrievalToolSelector().is_available():
            _safe_register(RetrievalToolSelector)
        else:
            logger.info(
                "selector_bootstrap: RetrievalToolSelector not available "
                "(sentence-transformers not installed) — skipping"
            )
    except ImportError as exc:
        logger.debug("selector_bootstrap: RetrievalToolSelector not importable: %s", exc)

    register_external_selector_plugins()
    logger.info(
        "selector_bootstrap: registered %d selector(s): %s",
        len(selector_registry.get_all()), list(selector_registry.get_all().keys()),
    )
