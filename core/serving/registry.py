"""
================================================================================
SERVING ENGINE REGISTRY  —  core/serving/registry.py
================================================================================

PURPOSE:
    Registry that maps ENGINE_TYPE strings to ServingEngineAdapter classes.
    Discovers plugin adapters via importlib.metadata entry points.
    Instantiates the correct adapter from YAML serving_engine config.

    The platform defines 'alems.engines.serving' as the discovery contract.
    Core does not register engine-specific implementations into that group.
    Optional engine plugin packages register themselves into it.

    chunk 35 registry contract:
        INV-5: dispatch deterministic from ENGINE_TYPE + config only.
        INV-6: duplicate ENGINE_TYPE = DuplicateRegistrationError.
        INV-7: no serving_engine in YAML = RemoteAPIAdapter (backward compat).

ENTRY POINT DISCOVERY:
    Plugin packages declare in their pyproject.toml:

        [project.entry-points."alems.engines.serving"]
        vllm = "alems_plugin_vllm.adapter:VLLMAdapter"

    ServingEngineRegistry.discover() is called once at startup.
    It loads all installed plugins and registers them.
    If a plugin fails to import, it is skipped with a warning (not a crash)
    unless the plugin is explicitly listed in config as required.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Optional, Type

from core.serving.serving_adapter import ServingEngineAdapter

logger = logging.getLogger(__name__)

# Entry-point group name. Defines the discovery contract.
# Core does not register into this group. Plugins do.
SERVING_ENGINE_EP_GROUP = "alems.engines.serving"

# Internal registry store. Populated by discover() and register().
_REGISTRY: dict[str, Type[ServingEngineAdapter]] = {}

# Track whether discovery has already run (idempotent).
_DISCOVERED = False


class ServingEngineRegistry:
    """
    Registry mapping ENGINE_TYPE strings to ServingEngineAdapter classes.

    Call discover() once at startup to load installed plugins.
    Call from_config() to instantiate the adapter for an experiment.
    """

    @staticmethod
    def discover() -> None:
        """
        Discover and register all installed serving engine plugins.

        Queries the 'alems.engines.serving' entry-point group.
        Safe to call multiple times — runs once then is idempotent.

        Failure handling (chunk 35 D1 contract):
            Import error on an optional plugin → log warning, skip.
            Duplicate ENGINE_TYPE → DuplicateRegistrationError (INV-6).
            Validation failure → log info, skip.
        """
        global _DISCOVERED
        if _DISCOVERED:
            return
        _DISCOVERED = True

        try:
            eps = entry_points(group=SERVING_ENGINE_EP_GROUP)
        except Exception as exc:
            logger.warning(
                "ServingEngineRegistry.discover: failed to query entry "
                "points for group '%s': %s. No plugins loaded.",
                SERVING_ENGINE_EP_GROUP,
                exc,
            )
            return

        for ep in eps:
            try:
                cls = ep.load()
            except Exception as exc:
                logger.warning(
                    "ServingEngineRegistry.discover: failed to load "
                    "plugin '%s' from '%s': %s. Skipping.",
                    ep.name,
                    ep.value,
                    exc,
                )
                continue

            if not issubclass(cls, ServingEngineAdapter):
                logger.warning(
                    "ServingEngineRegistry.discover: '%s' does not "
                    "subclass ServingEngineAdapter. Skipping.",
                    ep.name,
                )
                continue

            try:
                ServingEngineRegistry.register(cls)
            except ValueError as exc:
                # DuplicateRegistrationError — surface immediately.
                logger.error(
                    "ServingEngineRegistry.discover: registration failed "
                    "for plugin '%s': %s",
                    ep.name,
                    exc,
                )

        logger.info(
            "ServingEngineRegistry.discover: complete. "
            "Registered engines: %s",
            list(_REGISTRY.keys()),
        )

    @staticmethod
    def register(cls: Type[ServingEngineAdapter]) -> None:
        """
        Register a ServingEngineAdapter class.

        Args:
            cls: ServingEngineAdapter subclass with ENGINE_TYPE set.

        Raises:
            ValueError: if ENGINE_TYPE is empty or duplicate (INV-6).
        """
        if not cls.ENGINE_TYPE:
            raise ValueError(
                f"{cls.__name__} has no ENGINE_TYPE. "
                f"Set ENGINE_TYPE as a class attribute before registering."
            )
        if cls.ENGINE_TYPE in _REGISTRY:
            raise ValueError(
                f"Duplicate ENGINE_TYPE '{cls.ENGINE_TYPE}': already "
                f"registered as {_REGISTRY[cls.ENGINE_TYPE].__name__}. "
                f"INV-6: no silent overwrite."
            )
        _REGISTRY[cls.ENGINE_TYPE] = cls
        logger.debug(
            "ServingEngineRegistry: registered %s (ENGINE_TYPE=%s)",
            cls.__name__,
            cls.ENGINE_TYPE,
        )

    @staticmethod
    def from_config(serving_config: Optional[dict]) -> ServingEngineAdapter:
        """
        Instantiate and return the adapter for the given YAML config.

        Discovery must have run before this is called (bootstrap.py
        calls discover() as a side effect).

        Args:
            serving_config: the serving_engine: {...} dict from YAML.
                            None = RemoteAPIAdapter (backward compat, INV-7).

        Returns:
            Instantiated ServingEngineAdapter ready to use.
        """
        # Ensure discovery has run — safe to call multiple times.
        ServingEngineRegistry.discover()

        # INV-7: no config = backward compat = RemoteAPIAdapter.
        if not serving_config:
            logger.debug(
                "ServingEngineRegistry: no serving_engine config; "
                "using RemoteAPIAdapter (backward compat)."
            )
            return ServingEngineRegistry._make(
                "remote_api", {"name": "remote_api"}
            )

        engine_type = serving_config.get("type", "remote_api")

        if engine_type not in _REGISTRY:
            logger.warning(
                "ServingEngineRegistry: unknown ENGINE_TYPE '%s'. "
                "Is the plugin installed? "
                "e.g. pip install alems-plugin-%s  "
                "Falling back to RemoteAPIAdapter. "
                "Known types: %s.",
                engine_type,
                engine_type,
                list(_REGISTRY.keys()),
            )
            engine_type = "remote_api"

        adapter = ServingEngineRegistry._make(engine_type, serving_config)

        # If adapter reports not available, fall back gracefully.
        if not adapter.is_available() and engine_type != "remote_api":
            logger.warning(
                "ServingEngineRegistry: %s.is_available() = False. "
                "Falling back to RemoteAPIAdapter. "
                "Experiment continues without engine telemetry.",
                adapter.__class__.__name__,
            )
            adapter = ServingEngineRegistry._make("remote_api", serving_config)

        caps = adapter.capabilities()
        logger.info(
            "ServingEngineRegistry: using %s "
            "(ENGINE_TYPE=%s, name=%s, "
            "telemetry_scope=%s, kv_cache=%s, "
            "expert_tier=%s, queue=%s, prometheus=%s).",
            adapter.__class__.__name__,
            adapter.ENGINE_TYPE,
            adapter.get_name(),
            caps.telemetry_scope,
            caps.kv_cache_metrics,
            caps.expert_tier_metrics,
            caps.queue_metrics,
            caps.prometheus_metrics,
        )
        return adapter

    @staticmethod
    def _make(engine_type: str, config: dict) -> ServingEngineAdapter:
        """Instantiate the adapter class for the given engine_type."""
        cls = _REGISTRY[engine_type]
        return cls(config)

    @staticmethod
    def get_all() -> dict[str, Type[ServingEngineAdapter]]:
        """Return all registered adapter classes keyed by ENGINE_TYPE."""
        return dict(_REGISTRY)
