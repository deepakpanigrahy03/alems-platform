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

@dataclass
class StateReuseEvent:
    """
    Per-recovery cache reuse measurement.
    Maps to one row in state_reuse_events.

    Only populated when the serving engine provides request-level
    correlation (B2.4). Otherwise this object is never created.
    """

    # reuse_type must match state_reuse_taxonomy.reuse_type_id.
    reuse_type: str

    # recovery_id from the recovery_events row this reuse belongs to.
    # Set by the runner before DB insert; collector leaves it None.
    recovery_id: Optional[int] = None

    # attempt_id and run_id set by runner from execution context.
    attempt_id: Optional[int] = None
    run_id: Optional[int] = None

    # Human-readable source identifier (e.g. engine name, layer tag).
    reuse_source: Optional[str] = None

    # Token counts. None when engine does not expose them.
    tokens_reused: Optional[int] = None
    tokens_recomputed: Optional[int] = None

    # Computed by runner if both token counts are available.
    # Collector may pre-compute if engine reports fraction directly.
    reuse_fraction: Optional[float] = None

    # 1 = hit, 0 = miss, None = unknown.
    cache_hit: Optional[int] = None

    # Wall-clock time to query this cache (nanoseconds).
    cache_query_time_ns: Optional[int] = None

    # State size in bytes as reported by engine. None when unavailable.
    state_size_bytes: Optional[int] = None


@dataclass
class CacheStateSnapshot:
    """
    Engine-level aggregate cache metrics at a point in time.
    Maps to one row in cache_state_snapshots.

    Separate from StateReuseEvent per B2.3.
    Answers "what was the cache state at this moment?"
    NOT "how much was reused in this specific recovery?"
    """

    # run_id and attempt_id set by runner from execution context.
    run_id: Optional[int] = None
    attempt_id: Optional[int] = None

    # Nanosecond wall-clock of snapshot. Set by collector at collection time.
    timestamp_ns: Optional[int] = None

    # Engine identifier (e.g. 'vllm', 'groq', 'llama_cpp', 'anthropic').
    engine_name: Optional[str] = None

    # Cache type label as the engine names it. Free text — not FK to taxonomy.
    cache_type: Optional[str] = None

    # Capacity and occupancy. None when engine does not report.
    capacity_tokens: Optional[int] = None
    occupied_tokens: Optional[int] = None
    occupancy_fraction: Optional[float] = None

    # Aggregate hit rate since last engine reset.
    # This is engine-global, NOT per-request or per-recovery (B2.4).
    hit_rate_aggregate: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class CacheTelemetryCollector(ABC):
    """
    Abstract base class for cache telemetry collectors.

    Collects cache and state telemetry from the serving engine after
    a recovery completes.

    Does NOT write to the database — that is the runner's job.
    Does NOT execute the recovery — that is the harness's job.

    Implementations must be stateless: collect() may be called
    for different recoveries and must not accumulate mutable state.

    Chunk 35 INV-2 contract:
        collect() returns data objects, never writes core tables.
    """

    # Stable identity string used in YAML config and registry.
    # Override in every subclass.
    COLLECTOR_ID: str = ""

    @abstractmethod
    def collect(
        self,
        run_id: int,
        attempt_id: int,
        recovery_id: Optional[int],
        request_context: dict,
    ) -> tuple[list[StateReuseEvent], list[CacheStateSnapshot]]:
        """
        Collect cache telemetry for a completed recovery.

        Args:
            run_id:          runs.run_id for the current run.
            attempt_id:      goal_attempt.attempt_id being recovered.
            recovery_id:     recovery_events.recovery_id just recorded by runner.
                             None when collecting baseline (no prior recovery).
            request_context: dict with engine-specific correlation keys.
                             Keys vary by engine:
                               vLLM: {'request_id': str, 'endpoint': str}
                               Remote API: {'model': str}  (no request_id available)
                             Collector must handle missing keys gracefully.

        Returns:
            Tuple of:
                List[StateReuseEvent]:    per-recovery reuse (may be empty).
                List[CacheStateSnapshot]: engine-level snapshots (may be empty).

            Both lists may be empty. Empty is normal for most platforms (B2.5).
            Never raises — return ([], []) on any error.
        """
        ...

    def is_available(self) -> bool:
        """
        Return True if this collector can operate in the current environment.

        Override for collectors with optional dependencies (e.g. vLLM endpoint,
        Prometheus client library). Default returns True.
        NoOpCollector always returns True — it has no dependencies.
        """
        return True

    def get_name(self) -> str:
        """Return human-readable name for logging. Default uses COLLECTOR_ID."""
        return self.COLLECTOR_ID


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

_REGISTRY: dict[str, type[CacheTelemetryCollector]] = {
    NoOpCollector.COLLECTOR_ID: NoOpCollector,
}


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
