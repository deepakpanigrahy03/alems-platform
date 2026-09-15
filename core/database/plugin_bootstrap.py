#!/usr/bin/env python3
"""
================================================================================
DATABASE PLUGIN BOOTSTRAP  —  core/database/plugin_bootstrap.py
================================================================================

PURPOSE:
    Discover externally pip-installed database adapters via
    entry_points(group="alems.databases") and register them into
    DatabaseFactory, which already has register_adapter() (SPEC 35G
    Section 6 — "the only change is: database adapters register via
    entry_points instead of hardcoded import").

    Zero changes to core/database/base.py or core/database/sqlite_adapter.py.
    SQLite stays exactly as it is today: hardcoded in
    DatabaseFactory._adapters, not entry_point-discovered. This file
    only adds a path for EXTERNAL plugins (alems-db-postgres, etc.)
    on top of the existing hardcoded built-in.

IDENTITY MODEL:
    discover_plugins()'s register_fn receives (cls, config) — it does
    not pass the entry-point name. Readers/engines/scorers/tools all
    read identity off a class attribute (METHOD_ID/ENGINE_TYPE/
    SCORER_TYPE/TOOL_PROVIDER_TYPE) for this exact reason. Database
    adapters follow the same convention: an external DatabaseInterface
    subclass must declare ENGINE_TYPE as a class attribute (e.g.
    ENGINE_TYPE = "postgresql"). A plugin missing it is skipped with
    a clear log message, same failure semantics as every other family.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 6
================================================================================
"""

import logging

from core.database.factory import DatabaseFactory
from core.plugin_discovery import discover_plugins
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

_registered = False  # idempotency guard — mirrors is_empty() pattern used elsewhere


def _register_fn(cls, config: dict = None) -> None:
    """
    Register cls into DatabaseFactory keyed by its ENGINE_TYPE class
    attribute. Raises ValueError (caught by discover_plugins's caller
    semantics as a load failure) if ENGINE_TYPE is missing — this is
    the database-family equivalent of ScorerRegistry's "no SCORER_TYPE
    set" check.
    """
    engine_type = getattr(cls, "ENGINE_TYPE", "")
    if not engine_type:
        raise ValueError(
            f"Database adapter class {cls.__name__} has no ENGINE_TYPE "
            f"class attribute set. External database plugins must "
            f"declare ENGINE_TYPE (e.g. ENGINE_TYPE = 'postgresql')."
        )
    if engine_type in DatabaseFactory.list_supported_engines():
        raise ValueError(
            f"ENGINE_TYPE '{engine_type}' already registered. Cannot "
            f"register {cls.__name__} — INV-6: no silent replacement."
        )
    DatabaseFactory.register_adapter(engine_type, cls)
    logger.info(
        "database_plugin_bootstrap: registered %s as engine '%s'",
        cls.__name__, engine_type,
    )


def register_external_database_plugins() -> None:
    """
    Discover and register externally pip-installed database adapters
    via entry_points(group="alems.databases"). Additive to the
    hardcoded sqlite built-in. Idempotent.
    """
    global _registered
    if _registered:
        logger.debug("database_plugin_bootstrap: already registered — skipping")
        return

    names = discover_plugins(
        group="alems.databases",
        register_fn=_register_fn,
        core_version=_CORE_VERSION,
    )
    if names:
        logger.info(
            "database_plugin_bootstrap: %d external database engine(s) "
            "registered via entry_points: %s", len(names), names,
        )
    _registered = True
