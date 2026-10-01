#!/usr/bin/env python3
"""
================================================================================
ENGINE BOOTSTRAP  —  core/execution/adapters/bootstrap.py
================================================================================

Purpose:
    Register all built-in serving engine adapter classes into their family
    registries. Called once at startup from model_factory.py before any
    adapter is resolved.

    Phase 2 (this file): explicit registration via local imports.
    Phase 6 (Spec 35E):  entry_points discovery replaces this file.

    Adding a new engine adapter:
        (a) Create the adapter class file subclassing TextGenABC or MediaABC.
        (b) Declare ENGINE_TYPE on the class.
        (c) Add one import + _safe_register() call below.
        No model_factory.py changes needed.

Import isolation:
    Every import is local to its try/except block.
    Heavy dependencies (torch, llama-cpp-python, google-generativeai) are
    never imported on machines where they are not installed.

Author: Deepak Panigrahy
Spec:   SPEC 35B, Phase 2
================================================================================
"""

import logging
from core.readers.registry import AdapterRegistry
from alems_sdk.manifest import ORIGIN_EXTERNAL, ORIGIN_LOCAL, ORIGIN_RUNTIME
from core.registry.loader import load_group
from core.startup_banner import print_adapter_summary
from alems import __version__ as _CORE_VERSION
 
logger = logging.getLogger(__name__)
 
# ---------------------------------------------------------------------------
# Registry instances — one per adapter family.
# text_registry: ENGINE_TYPE -> TextGenABC subclass
# media_registry: ENGINE_TYPE -> MediaABC subclass
# ---------------------------------------------------------------------------
 
text_registry  = AdapterRegistry(family="text_engine")
media_registry = AdapterRegistry(family="media_engine")


_FAMILY_GROUPS = (
    ("text_engine",  "alems.engines.text",  text_registry),
    ("media_engine", "alems.engines.media", media_registry),
)


def _register_into(registry: AdapterRegistry):
    """Return a loader register function bound to one engine registry."""
    def _register(cls, config: dict = None) -> None:
        registry.register(cls)
    return _register


def register_text_adapters() -> None:
    """Register runtime text generation adapters (alems.engines.text)."""
    load_group("alems.engines.text", _register_into(text_registry), _CORE_VERSION,
               origins=(ORIGIN_RUNTIME,))


def register_media_adapters() -> None:
    """Register runtime media adapters (alems.engines.media)."""
    load_group("alems.engines.media", _register_into(media_registry), _CORE_VERSION,
               origins=(ORIGIN_RUNTIME,))


def register_external_engine_plugins() -> None:
    """
    Register external and sandbox local engines. Engines select by exact
    ENGINE_TYPE, so a plugin is usable once a provider config names it.
    """
    all_builtin = []
    all_external = []
    for family_name, group, registry in _FAMILY_GROUPS:
        builtin_before = list(registry.get_all().keys())
        names = load_group(
            group, _register_into(registry), _CORE_VERSION,
            origins=(ORIGIN_EXTERNAL, ORIGIN_LOCAL),
        )
        all_builtin.extend(builtin_before)
        all_external.extend(names)
        if names:
            logger.info(
                "bootstrap[%s]: %d external plugin(s) registered via entry_points",
                family_name, len(names),
            )
    print_adapter_summary("engines", all_builtin, all_external)


def register_all_adapters() -> None:
    """
    Register every engine adapter through the single plugin path.
    Idempotent: model_factory.py may trigger it more than once.
    """
    if not text_registry.is_empty() or not media_registry.is_empty():
        logger.debug("bootstrap: engine adapters already registered — skipping")
        return
    logger.info("bootstrap: registering all engine adapters (single plugin path)")
    register_text_adapters()
    register_media_adapters()
    register_external_engine_plugins()
    logger.info("bootstrap: engine adapter registration complete")