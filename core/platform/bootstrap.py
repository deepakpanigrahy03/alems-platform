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
from core.platform.registry import PlatformRegistry, DuplicatePlatformError
from core.plugin_discovery import discover_plugins
from core.startup_banner import print_adapter_summary
from alems import __version__ as _CORE_VERSION
 
logger = logging.getLogger(__name__)
 
# Module-level singleton — one registry for the process lifetime
platform_registry = PlatformRegistry()


def _safe_register(cls, config: dict = None) -> None:
    """Register cls, re-raising DuplicatePlatformError, swallowing others.
 
    Args:
        cls:    Platform adapter class to register.
        config: Validated plugin config dict from load_plugin_config().
                None for built-in platform adapters.
    """
    try:
        platform_registry.register(cls)
    except DuplicatePlatformError:
        raise
    except Exception as exc:
        logger.warning(
            "platform_bootstrap: failed to register %s: %s — skipping",
            getattr(cls, "__name__", repr(cls)), exc,
        )


def register_all_platform_adapters() -> None:
    """
    Register all built-in platform adapters.
    Idempotent — skips if already registered.
    """
    if not platform_registry.is_empty():
        logger.debug("platform_bootstrap: already registered — skipping")
        return

    logger.info("platform_bootstrap: registering platform adapters (SPEC 35C)")

    # Priority 100 — specific hardware detected first
    try:
        from core.platform.adapters.nvidia_grace import NVIDIAGraceAdapter
        _safe_register(NVIDIAGraceAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: NVIDIAGraceAdapter not importable: %s", exc)

    try:
        from core.platform.adapters.apple_silicon import AppleSiliconAdapter
        _safe_register(AppleSiliconAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: AppleSiliconAdapter not importable: %s", exc)

    # Priority 200 — specific OS/vendor combinations
    try:
        from core.platform.adapters.intel_linux import IntelLinuxAdapter
        _safe_register(IntelLinuxAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: IntelLinuxAdapter not importable: %s", exc)

    try:
        from core.platform.adapters.amd_linux import AMDLinuxAdapter
        _safe_register(AMDLinuxAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: AMDLinuxAdapter not importable: %s", exc)

    try:
        from core.platform.adapters.arm_linux import ARMLinuxAdapter
        _safe_register(ARMLinuxAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: ARMLinuxAdapter not importable: %s", exc)

    try:
        from core.platform.adapters.riscv_linux import RISCVLinuxAdapter
        _safe_register(RISCVLinuxAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: RISCVLinuxAdapter not importable: %s", exc)

    # Priority 900 — generic fallbacks
    try:
        from core.platform.adapters.generic_linux import GenericLinuxAdapter
        _safe_register(GenericLinuxAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: GenericLinuxAdapter not importable: %s", exc)

    # Priority 950 — synthetic last resort
    try:
        from core.platform.adapters.synthetic_platform import SyntheticPlatformAdapter
        _safe_register(SyntheticPlatformAdapter)
    except ImportError as exc:
        logger.debug("platform_bootstrap: SyntheticPlatformAdapter not importable: %s", exc)
 
    # SPEC 35E: external platform plugins installed via pip, discovered
    # through entry_points.
    builtin_before = list(platform_registry.get_all().keys())
    external_names = discover_plugins(
        group="alems.platforms",
        register_fn=lambda cls, cfg: _safe_register(cls, cfg),
        core_version=_CORE_VERSION,
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
