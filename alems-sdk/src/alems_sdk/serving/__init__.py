"""
alems_sdk.serving: serving engine telemetry contract (D3.3 serving engine point).

Physical home of the contract since 39.5.1a. core.serving.serving_adapter
re-exports these exact objects, so every core caller, isinstance check and
registry key is unchanged (strangler D2.2 completed in the final direction).

Rules carried over from chunk 35:
    INV-2: adapters return data objects; never write any store.
    INV-6: duplicate ENGINE_TYPE is rejected by the runtime registry.

telemetry_scope values:
    'request'     metric tied to a specific request_id (strongest)
    'interval'    Prometheus delta over the window around a call
    'process'     engine global aggregate since last reset
    'unavailable' engine does not expose this metric at all

Schema boundary (owned by the runtime, documented here for authors):
    KV cache state     -> cache_state_snapshots
    Expert tier        -> serving_runtime_snapshots
    Queue and health   -> serving_runtime_snapshots
    Token rate         -> serving_runtime_snapshots
Expert tier is model weight state, never request context state.

Zero imports from core or scripts (INV-14, D2.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# ServingEndpoint is part of the constructor contract (runtime resolves it).
from alems_sdk.serving.config import ServingEndpoint, resolve_endpoint


# ---------------------------------------------------------------------------
# Capability descriptor: what an adapter can actually measure.
# ---------------------------------------------------------------------------

@dataclass
class ServingCapabilities:
    """
    Declares what telemetry this adapter can actually provide.

    All fields default to False or 'unavailable'. An adapter overrides only
    what it truly supports; it never claims a capability it cannot deliver.
    'interval' scope is still useful attribution evidence even without a
    native request_id, so 'no request_id' never means 'no telemetry'.
    """

    request_execution: bool = False      # can the adapter issue requests at all
    queue_metrics: bool = False          # queue depth or health counters
    token_metrics: bool = False          # token throughput or TTFT
    kv_cache_metrics: bool = False       # KV cache occupancy or hit rate
    expert_tier_metrics: bool = False    # MoE expert placement (not KV cache)
    prometheus_metrics: bool = False     # Prometheus /metrics endpoint present
    request_correlation: bool = False    # telemetry tied to a request_id
    telemetry_scope: str = "unavailable"  # granularity, see module docstring


# ---------------------------------------------------------------------------
# Data transfer objects returned by adapter methods.
# The runtime maps these into rows; adapters never write.
# ---------------------------------------------------------------------------

@dataclass
class RequestMetrics:
    """Per request metrics; meaningful when telemetry_scope == 'request'."""

    request_id: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prefix_cache_hit: Optional[bool] = None
    prefix_cache_hit_tokens: int = 0
    kv_cache_blocks_used: int = 0
    ttft_ms: Optional[float] = None
    total_time_ms: Optional[float] = None


@dataclass
class CacheState:
    """
    Engine level KV cache aggregate metrics.

    hit_rate_aggregate is engine global; never interpret it as per request or
    per recovery reuse without a correlation key.
    """

    cache_type: str = ""
    capacity_tokens: int = 0
    occupied_tokens: int = 0
    occupancy_fraction: float = 0.0
    hit_rate_aggregate: float = 0.0
    num_evictions: int = 0


@dataclass
class ExpertTierState:
    """
    MoE expert placement across the storage hierarchy.

    Must never be stored as KV cache state: expert placement is model weight
    state, a different semantic object.
    """

    vram_bytes: int = 0
    ram_bytes: int = 0
    disk_bytes: int = 0
    vram_fraction: float = 0.0           # fractions sum to at most 1.0
    ram_fraction: float = 0.0
    disk_fraction: float = 0.0
    expert_hit_rate: float = 0.0         # learning cache hit rate


@dataclass
class QueueState:
    """Engine queue and health counters."""

    active: int = 0
    waiting: int = 0
    completed: int = 0
    rejected: int = 0
    queue_wait_ms: Optional[float] = None


@dataclass
class TokenRateState:
    """Throughput and latency telemetry."""

    tokens_per_second: Optional[float] = None
    ttft_ms: Optional[float] = None
    prompt_tokens_total: int = 0
    generation_tokens_total: int = 0


@dataclass
class EngineInfo:
    """Static engine metadata, collected once at startup for provenance."""

    engine_name: str = ""
    engine_type: str = ""
    version: str = ""
    model_loaded: str = ""
    gpu_count: int = 0
    capabilities: ServingCapabilities = field(
        default_factory=ServingCapabilities
    )


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class ServingEngineAdapter(ABC):
    """
    Generic interface for serving engine telemetry.

    One plugin package implements this ABC for one engine type. ENGINE_TYPE is
    the registry key and must equal the YAML serving_engine.type value and the
    entry point name in the plugin's pyproject.toml.

    Construction contract (39.5.1a):
        The runtime resolves endpoint configuration (configuration precedence
        belongs to the runtime, C-ORIGIN) and passes it as endpoint. When an
        adapter is constructed outside the runtime (tests, notebooks) endpoint
        is None and the adapter may call resolve_endpoint(config, ENGINE_TYPE)
        which applies the experiment layer and the environment only.
    """

    ENGINE_TYPE: str = ""

    def __init__(self, config: dict, endpoint: Optional[ServingEndpoint] = None):
        """
        Initialise from the serving_engine config block.

        Args:
            config: the serving_engine: {...} dict from the profile.
            endpoint: endpoint configuration resolved by the runtime, or None.
        """
        self.config = config
        self._name = config.get("name", self.ENGINE_TYPE)
        # Kept as given; adapters decide how to fall back when None.
        self.endpoint_config = endpoint

    @abstractmethod
    def capabilities(self) -> ServingCapabilities:
        """Full capability surface. Called once at startup. Never raises."""
        ...

    @abstractmethod
    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        """Per request metrics, or empty RequestMetrics(). Never raises."""
        ...

    @abstractmethod
    def get_cache_state(self) -> CacheState:
        """KV cache aggregate metrics, or empty CacheState(). Never raises."""
        ...

    @abstractmethod
    def get_expert_tier_state(self) -> ExpertTierState:
        """MoE expert placement, or empty ExpertTierState(). Never raises."""
        ...

    @abstractmethod
    def get_queue_state(self) -> QueueState:
        """Queue and health counters, or empty QueueState(). Never raises."""
        ...

    @abstractmethod
    def get_engine_info(self) -> EngineInfo:
        """Static engine metadata. Called once at startup. Never raises."""
        ...

    def is_available(self) -> bool:
        """True if the adapter can operate here. Default True."""
        return True

    def get_name(self) -> str:
        """Human readable name for logging."""
        return self._name


__all__ = [
    "ServingCapabilities",
    "RequestMetrics",
    "CacheState",
    "ExpertTierState",
    "QueueState",
    "TokenRateState",
    "EngineInfo",
    "ServingEngineAdapter",
    "ServingEndpoint",
    "resolve_endpoint",
]
