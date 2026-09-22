"""
================================================================================
REMOTE API ADAPTER  —  core/serving/remote_api_adapter.py
================================================================================

PURPOSE:
    Default ServingEngineAdapter for remote API providers:
    Groq, OpenAI, Anthropic, Gemini, and any OpenAI-compatible endpoint.

    This is the only adapter that lives in core. All other engines are
    optional pip plugins in separate packages.

    Remote APIs expose no cache telemetry to the caller.
    capabilities().telemetry_scope = 'unavailable'.
    All get_*() methods return empty/default objects. Never raises.

    B3.4: remote API adapter returns empty metrics, no errors.
    INV-7: used as fallback when no serving_engine config is present.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from core.serving.serving_adapter import (
    ServingEngineAdapter,
    ServingCapabilities,
    RequestMetrics,
    CacheState,
    ExpertTierState,
    QueueState,
    TokenRateState,
    EngineInfo,
)

logger = logging.getLogger(__name__)


class RemoteAPIAdapter(ServingEngineAdapter):
    """
    Default adapter for remote API providers (Groq, OpenAI, Anthropic, Gemini).

    No runtime telemetry is available from remote APIs.
    All capability flags are False. telemetry_scope = 'unavailable'.
    All get_*() calls return empty defaults.

    This produces the same result as NoOpCollector in B2:
    cache_state_snapshots and state_reuse_events stay empty,
    which is correct per B2.5.

    ENGINE_TYPE = 'remote_api'
    """

    ENGINE_TYPE = "remote_api"

    def __init__(self, config: dict):
        super().__init__(config)
        logger.info(
            "ServingEngine: RemoteAPIAdapter active (name=%s). "
            "No telemetry available on remote API platforms (B2.5).",
            self._name,
        )

    def capabilities(self) -> ServingCapabilities:
        """Remote APIs expose no runtime telemetry."""
        return ServingCapabilities(
            request_execution=True,   # we do issue requests through the API
            queue_metrics=False,
            token_metrics=False,
            kv_cache_metrics=False,
            expert_tier_metrics=False,
            prometheus_metrics=False,
            request_correlation=False,
            telemetry_scope="unavailable",
        )

    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        """No per-request telemetry on remote APIs."""
        return RequestMetrics()

    def get_cache_state(self) -> CacheState:
        """No KV cache visibility on remote APIs."""
        return CacheState()

    def get_expert_tier_state(self) -> ExpertTierState:
        """No expert tier data on remote APIs."""
        return ExpertTierState()

    def get_queue_state(self) -> QueueState:
        """No queue telemetry on remote APIs."""
        return QueueState()

    def get_engine_info(self) -> EngineInfo:
        """Return minimal info from YAML config only."""
        return EngineInfo(
            engine_name=self._name,
            engine_type=self.ENGINE_TYPE,
            version="",
            model_loaded="",
            gpu_count=0,
            capabilities=self.capabilities(),
        )

    def is_available(self) -> bool:
        """Always True — no local dependencies."""
        return True
