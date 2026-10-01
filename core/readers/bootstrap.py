#!/usr/bin/env python3
"""
================================================================================
READER BOOTSTRAP  —  core/readers/bootstrap.py
================================================================================

Purpose:
    Register all REAL built-in reader classes into their family registries.
    Dummies are NOT registered — they are factory-level fallbacks.
    Called once from energy_engine.py before any ReaderFactory call.

    Phase 1 (this file): explicit registration via local imports.
    Phase 6 (Spec 35E):  entry_points discovery replaces this file.

    Adding a new internal reader:
        (a) Create the reader class file.
        (b) Add one import + _safe_register() call below.
        No factory.py changes needed.

Import isolation:
    Every import is local to its try/except block.
    Platform-specific readers (SPBM, IOKit) are never imported on
    machines where they would fail at import time. PAC-2 preserved.

Author: Deepak Panigrahy
Spec:   SPEC 35A, Phase 1
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
# Registry instances — one per real reader family.
# Typed at usage site in factory.py via AdapterRegistry[FamilyABC].
# ---------------------------------------------------------------------------

energy_registry    = AdapterRegistry(family="energy")
cpu_registry       = AdapterRegistry(family="cpu")
thermal_registry   = AdapterRegistry(family="thermal")
turbostat_registry = AdapterRegistry(family="turbostat")
msr_registry       = AdapterRegistry(family="msr")
scheduler_registry = AdapterRegistry(family="scheduler")
disk_registry      = AdapterRegistry(family="disk")

# NIC: not in registry in 35A — NICCollector handles selection internally.
# Will be added when NICReaderABC is stable across all platforms.

# ---------------------------------------------------------------------------
# _safe_register: registration that never silently hides programming errors
# ---------------------------------------------------------------------------

# One table: family, entry point group, registry. Synthetic readers are in
# their family groups (pyproject). Order never decides selection: select()
# raises on priority ties.
_FAMILY_GROUPS = (
    ("energy",    "alems.readers.energy",    energy_registry),
    ("cpu",       "alems.readers.cpu",       cpu_registry),
    ("thermal",   "alems.readers.thermal",   thermal_registry),
    ("turbostat", "alems.readers.turbostat", turbostat_registry),
    ("msr",       "alems.readers.msr",       msr_registry),
    ("scheduler", "alems.readers.scheduler", scheduler_registry),
    ("disk",      "alems.readers.disk",      disk_registry),
)


def _register_into(registry: AdapterRegistry):
    """Return a loader register function bound to one family registry."""
    def _register(cls, config: dict = None) -> None:
        # Config is injected by the factory at instantiation, not here.
        registry.register(cls)
    return _register


def register_external_reader_plugins() -> None:
    """
    Register external and sandbox local readers for every family.

    Runs after runtime readers, so builtin_before is the built-in set
    shown by the startup banner.
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
    print_adapter_summary("readers", all_builtin, all_external)


def register_all_readers() -> None:
    """
    Register every reader through the single plugin path (SPEC 35 step 1).

    Called from energy_engine.py before any ReaderFactory method. Idempotent:
    a second call in the same process registers nothing.
    """
    if any(not r.is_empty() for _f, _g, r in _FAMILY_GROUPS):
        logger.debug("bootstrap: readers already registered — skipping")
        return
    logger.info("bootstrap: registering all readers (single plugin path)")
    for _family, group, registry in _FAMILY_GROUPS:
        load_group(group, _register_into(registry), _CORE_VERSION, origins=(ORIGIN_RUNTIME,))
    register_external_reader_plugins()
    logger.info("bootstrap: registration complete")