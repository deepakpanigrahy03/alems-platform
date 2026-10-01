#!/usr/bin/env python3
"""
================================================================================
PLATFORM BOOTSTRAP  —  core/platform/bootstrap.py
================================================================================

Purpose:
    Register all built-in platform adapters into the platform registry.
    Called once at startup before detection runs.

    Each adapter is imported in a local try/except block so platform-specific
    imports that fail on other machines are skipped gracefully.

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part A
================================================================================
"""

import logging
from core.platform.registry import PlatformRegistry
from alems_sdk.manifest import ORIGIN_EXTERNAL, ORIGIN_LOCAL, ORIGIN_RUNTIME
from core.registry.loader import load_group
from core.startup_banner import print_adapter_summary
from alems import __version__ as _CORE_VERSION
 
logger = logging.getLogger(__name__)
 
# Module-level singleton — one registry for the process lifetime
platform_registry = PlatformRegistry()


def _register(cls, config: dict = None) -> None:
    """Register one platform adapter; failures become loader refusals (WP 1a.3 section 5)."""
    platform_registry.register(cls)


def register_all_platform_adapters() -> None:
    """
    Register every platform adapter through the single plugin path.

    Idempotent: skips if already registered. Runtime adapters load first,
    then external and sandbox local ones, so the startup banner keeps its
    built-in versus plugin split.
    """
    if not platform_registry.is_empty():
        logger.debug("platform_bootstrap: already registered — skipping")
        return
    logger.info("platform_bootstrap: registering platform adapters (SPEC 35C)")
    load_group("alems.platforms", _register, _CORE_VERSION, origins=(ORIGIN_RUNTIME,))
    builtin_before = list(platform_registry.get_all().keys())
    external_names = load_group(
        "alems.platforms", _register, _CORE_VERSION,
        origins=(ORIGIN_EXTERNAL, ORIGIN_LOCAL),
    )
    if external_names:
        logger.info(
            "platform_bootstrap: %d external plugin(s) registered via entry_points",
            len(external_names),
        )
    print_adapter_summary("platforms", builtin_before, external_names)
    logger.info(
        "platform_bootstrap: registered %d platform adapters",
        len(platform_registry.get_all()),
    )