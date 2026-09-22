"""
================================================================================
SERVING ENGINE ADAPTER  —  core/serving/serving_adapter.py
================================================================================

PURPOSE:
    Abstract base class and data contracts for serving engine telemetry.

    Core defines the contract. Engine packages implement it as plugins.
    Adding a new engine = one pip package. Zero core changes. Zero schema
    changes. Zero runner changes.

    chunk 35 invariants honoured:
        INV-2: adapter returns data; never writes DB.
        INV-5: dispatch deterministic from ENGINE_TYPE string.
        INV-6: duplicate ENGINE_TYPE = DuplicateRegistrationError.
        INV-7: no serving_engine config = RemoteAPIAdapter (backward compat).

CAPABILITY MODEL (review 2026-09-22, point 11):
    has_request_level_correlation() boolean is gone.
    capabilities() returns ServingCapabilities — the full surface of what
    this adapter can actually measure. EngineBackedCollector reads this
    to decide what to collect and which table to write.

    telemetry_scope values:
        'request'     — metric tied to a specific request_id (strongest)
        'interval'    — Prometheus delta over the window around a call
        'process'     — engine-global aggregate since last reset
        'unavailable' — engine does not expose this metric at all

SCHEMA BOUNDARY (review 2026-09-22, point 2):
    KV cache state  → cache_state_snapshots  (B2, existing)
    Expert tier     → serving_runtime_snapshots  (v110, new)
    Queue / health  → serving_runtime_snapshots  (v110, new)
    Token rate      → serving_runtime_snapshots  (v110, new)
    Per-recovery reuse → state_reuse_events  (B2, existing)

    Expert tier (Colibri VRAM/RAM/NVMe expert placement) is model-weight
    state, NOT request-context state. Never store it in cache_state_snapshots.

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
# Capability descriptor — what an adapter can actually measure.
# Returned by capabilities(). Read by EngineBackedCollector to decide
# what to collect and where to write it.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ServingCapabilities:
    """
    Declares what telemetry this adapter can actually provide.

    All fields default to False / 'unavailable'.
    Each adapter overrides only what it truly supports — never claim
    a capability the adapter cannot deliver.

    telemetry_scope: granularity of available telemetry.
        'request'     — per-request_id attribution possible.
        'interval'    — Prometheus snapshot delta around the call.
        'process'     — engine-global since last reset only.
        'unavailable' — no runtime telemetry available.

    Note: 'interval' scope is still useful for B2/B3.
    A Prometheus delta over the window surrounding a recovery is
    meaningful attribution even without a native request_id.
    Do not conflate 'no request_id' with 'no useful telemetry'.
    """

    # Can the adapter issue inference requests at all?
    request_execution: bool = False

    # Does the engine expose queue depth / health counters?
    queue_metrics: bool = False

    # Does the engine expose token throughput / TTFT?
    token_metrics: bool = False

    # Does the engine expose KV cache occupancy / hit rate?
    kv_cache_metrics: bool = False

    # Does the engine expose MoE expert tier placement?
    # Colibri only. NOT the same as KV cache.
    expert_tier_metrics: bool = False

    # Does the engine expose a Prometheus /metrics endpoint?
    prometheus_metrics: bool = False

    # Can telemetry be tied to a specific request_id?
    request_correlation: bool = False

    # Telemetry granularity — see docstring above.
    telemetry_scope: str = "unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# Data transfer objects returned by adapter methods.
# EngineBackedCollector maps these into DB rows.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RequestMetrics:
    """
    Per-request metrics from the serving engine.

    Populated when capabilities().telemetry_scope == 'request'.
    All fields default to None/0 — callers must check before using.
    """
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
    Engine-level KV cache aggregate metrics.

    Maps to cache_state_snapshots (B2) when capacity_tokens > 0.
    Also contributes to serving_runtime_snapshots with
    snapshot_type='kv_cache' (v110).

    B2.4: hit_rate_aggregate is engine-global. Never interpret as
    per-request or per-recovery reuse without a correlation key.
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

    Colibri-specific. Maps to serving_runtime_snapshots with
    snapshot_type='expert_tier'.

    MUST NOT be stored in cache_state_snapshots. Expert placement is
    model-weight state, not request-context state. They are different
    semantic objects — mixing them corrupts recovery analysis.
    """
    # Bytes of expert weights resident in each tier.
    vram_bytes: int = 0
    ram_bytes: int = 0
    disk_bytes: int = 0
    # Fraction of total expert weights in each tier (sum <= 1.0).
    vram_fraction: float = 0.0
    ram_fraction: float = 0.0
    disk_fraction: float = 0.0
    # Expert hit rate from the learning cache (Colibri PIN mechanism).
    expert_hit_rate: float = 0.0


@dataclass
class QueueState:
    """
    Engine queue / health counters.

    Maps to serving_runtime_snapshots with snapshot_type='queue'.
    Available on: Colibri (/health), vLLM (Prometheus), SGLang (Prometheus).
    """
    active: int = 0
    waiting: int = 0
    completed: int = 0
    rejected: int = 0
    # Queue wait in ms — Colibri exposes via x-colibri-queue-wait-ms header.
    queue_wait_ms: Optional[float] = None


@dataclass
class TokenRateState:
    """
    Throughput and latency telemetry.

    Maps to serving_runtime_snapshots with snapshot_type='token_rate'.
    """
    tokens_per_second: Optional[float] = None
    ttft_ms: Optional[float] = None
    prompt_tokens_total: int = 0
    generation_tokens_total: int = 0


@dataclass
class EngineInfo:
    """
    Static metadata about this engine instance.
    Collected once at startup for logging and provenance.
    """
    engine_name: str = ""
    engine_type: str = ""
    version: str = ""
    model_loaded: str = ""
    gpu_count: int = 0
    capabilities: ServingCapabilities = field(
        default_factory=ServingCapabilities
    )


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class ServingEngineAdapter(ABC):
    """
    Generic interface for serving engine telemetry.

    Each plugin package implements this ABC for one engine type.
    The runner instantiates the adapter once at startup from YAML config
    via ServingEngineRegistry.from_config().

    ENGINE_TYPE must be set as a class attribute in every subclass.
    It is the key in the registry and must match the YAML
    serving_engine.type value and the entry-point name in the plugin's
    pyproject.toml.

    INV-2: adapter returns data objects; never writes DB.
    INV-6: duplicate ENGINE_TYPE raises at registration time.
    B3.5: per-request energy attribution in batched serving is OUT OF SCOPE.
    """

    ENGINE_TYPE: str = ""

    def __init__(self, config: dict):
        """
        Initialise from YAML serving_engine config block.

        Args:
            config: the serving_engine: {...} dict from YAML.
                    Subclasses read their own keys from it.
        """
        self.config = config
        self._name = config.get("name", self.ENGINE_TYPE)

    @abstractmethod
    def capabilities(self) -> ServingCapabilities:
        """
        Return the full capability surface of this adapter.

        Called once at startup. Result is logged for provenance.
        Never raises.
        """
        ...

    @abstractmethod
    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        """
        Return per-request metrics for the given request_id.

        Only meaningful when capabilities().request_correlation is True.
        Returns empty RequestMetrics() otherwise. Never raises.
        """
        ...

    @abstractmethod
    def get_cache_state(self) -> CacheState:
        """
        Return current KV cache aggregate metrics.

        Only meaningful when capabilities().kv_cache_metrics is True.
        Returns empty CacheState() otherwise. Never raises.
        """
        ...

    @abstractmethod
    def get_expert_tier_state(self) -> ExpertTierState:
        """
        Return MoE expert placement across storage tiers.

        Only meaningful when capabilities().expert_tier_metrics is True.
        Returns empty ExpertTierState() otherwise. Never raises.

        Result maps to serving_runtime_snapshots snapshot_type='expert_tier'.
        MUST NOT be written to cache_state_snapshots.
        """
        ...

    @abstractmethod
    def get_queue_state(self) -> QueueState:
        """
        Return queue depth and health counters.

        Only meaningful when capabilities().queue_metrics is True.
        Returns empty QueueState() otherwise. Never raises.
        """
        ...

    @abstractmethod
    def get_engine_info(self) -> EngineInfo:
        """
        Return static engine metadata. Called once at startup. Never raises.
        """
        ...

    def is_available(self) -> bool:
        """
        Return True if this adapter can operate in the current environment.

        Override for adapters with optional dependencies (e.g. a running
        vLLM endpoint). Default returns True.
        """
        return True

    def get_name(self) -> str:
        """Return human-readable name for logging."""
        return self._name
