"""
alems_sdk.cache_telemetry: cache telemetry collector contract (harness.collectors).

Physical home since 39.5.1a.2; core.telemetry.cache_collector re-exports
these objects. NoOpCollector and the registry stay in core.
Collectors return data objects and never write any store (INV-2, chunk 35).
Zero imports from core or scripts (INV-14).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple
from alems_sdk.config_schema import Configurable

@dataclass
class StateReuseEvent:
    """
    Per recovery cache reuse measurement; one row in state_reuse_events.

    Created only when the serving engine provides request level correlation.
    """

    # Must match state_reuse_taxonomy.reuse_type_id.
    reuse_type: str

    # recovery_events row this reuse belongs to; set by the runner.
    recovery_id: Optional[int] = None

    # Set by the runner from execution context.
    attempt_id: Optional[int] = None
    run_id: Optional[int] = None

    # Human readable source identifier (engine name, layer tag).
    reuse_source: Optional[str] = None

    # Token counts; None when the engine does not expose them.
    tokens_reused: Optional[int] = None
    tokens_recomputed: Optional[int] = None

    # Computed by the runner when both counts exist, or reported by the engine.
    reuse_fraction: Optional[float] = None

    # 1 hit, 0 miss, None unknown.
    cache_hit: Optional[int] = None

    # Wall clock time to query this cache (ns).
    cache_query_time_ns: Optional[int] = None

    # State size in bytes as reported by the engine.
    state_size_bytes: Optional[int] = None


@dataclass
class CacheStateSnapshot:
    """
    Engine level aggregate cache metrics at a point in time; one row in
    cache_state_snapshots. Answers "what was the cache state now", not "how
    much was reused in this recovery".
    """

    # Set by the runner from execution context.
    run_id: Optional[int] = None
    attempt_id: Optional[int] = None

    # Wall clock ns of the snapshot, set by the collector.
    timestamp_ns: Optional[int] = None

    # Engine identifier, e.g. 'vllm', 'llama_cpp'.
    engine_name: Optional[str] = None

    # Cache type label as the engine names it (free text).
    cache_type: Optional[str] = None

    # Capacity and occupancy; None when not reported.
    capacity_tokens: Optional[int] = None
    occupied_tokens: Optional[int] = None
    occupancy_fraction: Optional[float] = None

    # Engine global hit rate since last reset; never per request or recovery.
    hit_rate_aggregate: Optional[float] = None


class CacheTelemetryCollector(Configurable, ABC):
    """
    Collects cache and state telemetry after a recovery completes.

    Writes nothing (the runner does) and executes nothing (the harness does).
    Implementations are stateless across recoveries.
    """

    # Stable identity used in YAML config and the registry; override.
    COLLECTOR_ID: str = ""

    @abstractmethod
    def collect(
        self,
        run_id: int,
        attempt_id: int,
        recovery_id: Optional[int],
        request_context: dict,
    ) -> Tuple[List[StateReuseEvent], List[CacheStateSnapshot]]:
        """
        Collect cache telemetry for a completed recovery.

        Args:
            run_id:          runs.run_id of the current run.
            attempt_id:      goal_attempt.attempt_id being recovered.
            recovery_id:     recovery_events.recovery_id just recorded, or None
                             for a baseline collection.
            request_context: engine specific correlation keys; missing keys
                             are handled gracefully.

        Returns:
            (reuse events, snapshots); both may be empty. Never raises.
        """
        ...

    def is_available(self) -> bool:
        """True if the collector can operate here. Default True."""
        return True

    def get_name(self) -> str:
        """Human readable name; defaults to COLLECTOR_ID."""
        return self.COLLECTOR_ID


__all__ = ["StateReuseEvent", "CacheStateSnapshot", "CacheTelemetryCollector"]
