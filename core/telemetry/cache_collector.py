"""
================================================================================
CACHE TELEMETRY COLLECTOR  —  core/telemetry/cache_collector.py
================================================================================

PURPOSE:
    Abstract base class for cache and state telemetry collection.
    Follows chunk 35 adapter pattern: ABC + COLLECTOR_ID + registry.
    INV-2: collector provides capability, never writes core tables.
    INV-3: recording to state_reuse_events and cache_state_snapshots
           is the RUNNER's job, not the collector's.

    The collector is called AFTER a recovery attempt completes.
    It returns two lists: per-recovery reuse events and engine-level
    snapshots. The runner inserts them.

    B2.4 DATA INTEGRITY RULE:
    Aggregate engine metrics MUST NOT be interpreted as recovery-level
    reuse unless a request/recovery correlation key exists.
    If the engine provides only aggregate metrics (e.g. hit_rate_aggregate),
    the collector populates CacheStateSnapshot only and returns an empty
    StateReuseEvent list. This rule is enforced HERE, not in SQL.

    B2.5: telemetry is OPTIONAL per platform.
    NoOpCollector is the default for all current platforms.
    Empty lists are normal. No errors raised.

PLATFORMS:
    Remote API (Groq, OpenAI, Anthropic) → NoOpCollector (empty lists)
    vLLM local aggregate only             → VllmCollector → snapshots only
    vLLM local with request_id            → VllmCollector → events + snapshots
    llama.cpp local                       → TBD (NoOpCollector until adapter built)

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data transfer objects returned by collect().
# The runner maps these to DB rows.
# ─────────────────────────────────────────────────────────────────────────────

# Contracts live in the SDK since 39.5.1a.2; NoOpCollector and the registry
# below use them.
from alems_sdk.cache_telemetry import (  # noqa: E402
    CacheStateSnapshot,
    CacheTelemetryCollector,
    StateReuseEvent,
)

# ─────────────────────────────────────────────────────────────────────────────
# Default implementation: no-op
# ─────────────────────────────────────────────────────────────────────────────

class NoOpCollector(CacheTelemetryCollector):
    """
    Default collector for platforms without cache visibility.

    Used for:
      - Remote API platforms (Groq, OpenAI, Anthropic): no request-level
        telemetry is exposed by the API.
      - Local engines not yet instrumented (llama.cpp, ollama).

    Always returns empty lists. Never raises. Zero overhead.
    B2.5: empty tables are normal and expected on these platforms.
    """

    COLLECTOR_ID = "noop"

    def collect(
        self,
        run_id: int,
        attempt_id: int,
        recovery_id: Optional[int],
        request_context: dict,
    ) -> tuple[list[StateReuseEvent], list[CacheStateSnapshot]]:
        """Return empty lists. No telemetry available on this platform."""
        # Intentionally silent — do not log on every call.
        # The runner logs at startup which collector is active.
        return ([], [])


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# Follows same pattern as RecoveryPolicyRegistry and ScorerRegistry.
# ─────────────────────────────────────────────────────────────────────────────

# Filled only through the single plugin path (WP 1a.3, INV-2); see ensure_loaded.
_REGISTRY: dict[str, type[CacheTelemetryCollector]] = {}
_LOADED = False


class CacheTelemetryRegistry:
    """
    Registry mapping COLLECTOR_ID strings to CacheTelemetryCollector classes.

    Selected by YAML: serving_engine.telemetry_collector: noop | vllm | ...
    Default: noop (B2.5 — no telemetry on most platforms).

    When chunk 31 plugin packaging lands, add entry_points discovery here.
    """

    @staticmethod
    def get(collector_id: Optional[str]) -> CacheTelemetryCollector:
        """
        Instantiate and return the collector for the given COLLECTOR_ID.

        Args:
            collector_id: COLLECTOR_ID string from YAML config.
                          None or empty string resolves to 'noop'.

        Returns:
            Instantiated CacheTelemetryCollector ready to call collect().

        Raises:
            ValueError if collector_id is unknown and not empty.
        """
        # Default to noop for all platforms without explicit config (B2.5).
        CacheTelemetryRegistry.ensure_loaded()
        resolved = collector_id or "noop"

        cls = _REGISTRY.get(resolved)
        if cls is None:
            raise ValueError(
                f"Unknown telemetry collector '{resolved}'. "
                f"Known collectors: {list(_REGISTRY.keys())}. "
                f"Add a collector class and call CacheTelemetryRegistry.register()."
            )
        return cls()

    @staticmethod
    def ensure_loaded() -> None:
        """
        Load alems.harness.collectors once through load_group (WP 1a.3 C6).

        goal_execution_manager calls this at import, so get() during
        recovery never reads package metadata inside a measurement
        window (master 5.2a, G62).
        """
        global _LOADED
        if _LOADED:
            return
        # Set first: a refused group must not be retried on every get().
        _LOADED = True
        from core.registry.loader import load_group  # late: avoid import cycle
        from alems import __version__ as core_version
        load_group(
            "alems.harness.collectors",
            lambda cls, cfg: CacheTelemetryRegistry.register(cls),
            core_version,
        )

    @staticmethod
    def register(cls: type[CacheTelemetryCollector]) -> None:
        """
        Register a custom collector class.

        Args:
            cls: CacheTelemetryCollector subclass with COLLECTOR_ID set.

        Raises:
            ValueError on duplicate COLLECTOR_ID (INV-6: no silent replacement).
            ValueError if COLLECTOR_ID is empty.
        """
        if not cls.COLLECTOR_ID:
            raise ValueError(
                f"Collector class {cls.__name__} has no COLLECTOR_ID set. "
                f"Set COLLECTOR_ID as a class attribute before registering."
            )
        if cls.COLLECTOR_ID in _REGISTRY:
            raise ValueError(
                f"Duplicate COLLECTOR_ID '{cls.COLLECTOR_ID}': "
                f"already registered as {_REGISTRY[cls.COLLECTOR_ID].__name__}. "
                f"INV-6: no silent replacement."
            )
        _REGISTRY[cls.COLLECTOR_ID] = cls
        logger.debug(
            "CacheTelemetryRegistry: registered %s (COLLECTOR_ID=%s)",
            cls.__name__,
            cls.COLLECTOR_ID,
        )

    @staticmethod
    def get_all() -> dict[str, type[CacheTelemetryCollector]]:
        """Return all registered collector classes keyed by COLLECTOR_ID."""
        return dict(_REGISTRY)
