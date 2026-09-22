"""
================================================================================
COLIBRI ADAPTER  —  alems_plugin_colibri/adapter.py
================================================================================

PURPOSE:
    ServingEngineAdapter for Colibri inference server.
    Capability-limited: /health JSON polling for queue counters.
    No /metrics endpoint — prometheus_metrics=False.
    expert_tier_metrics=False until Colibri exposes a stable /v1/stats API.

CONFIG RESOLUTION (three layers, highest first):
    1. experiment YAML  serving_engine.endpoint
    2. config/adapters.yaml  engines.colibri.endpoint
    3. env var  ALEMS_COLIBRI_URL  (set in .alems-env)

ENDPOINT REALITY (Colibri binary):
    /v1/models  → 200  (engine alive)
    /health     → 200  JSON  {queued: N, running: N, completed: N, ...}
    /metrics    → 404  (NOT PRESENT)

    Discovery result: queue_metrics=True, all Prometheus caps False.
    EngineBackedCollector writes queue snapshot to serving_runtime_snapshots.

EXPERT TIER NOTE:
    Colibri places MoE expert weights across VRAM/RAM/NVMe tiers.
    This state is visible in the Colibri web dashboard only.
    No stable programmatic API exposed yet.
    expert_tier_metrics will be set True once /v1/stats is confirmed stable.
    ExpertTierState dataclass is ready — zero code change required at that point.

QUEUE WAIT TIME:
    Colibri sets x-colibri-queue-wait-ms response header on inference replies.
    The adapter reads this header from the last inference response when available.
    Stored in QueueState.queue_wait_ms for serving_runtime_snapshots.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from core.serving.serving_adapter import (
    CacheState,
    EngineInfo,
    ExpertTierState,
    QueueState,
    RequestMetrics,
    ServingCapabilities,
    ServingEngineAdapter,
    TokenRateState,
)
from core.serving.adapter_config import AdapterConfig
from core.serving.discovery import CapabilityDiscoveryMixin

logger = logging.getLogger(__name__)


class ColibriAdapter(CapabilityDiscoveryMixin, ServingEngineAdapter):
    """
    ServingEngineAdapter for Colibri.
    Queue metrics from /health JSON.
    Expert tier deferred until stable API.
    ENGINE_TYPE = 'colibri'
    """

    ENGINE_TYPE = "colibri"

    def __init__(self, config: dict):
        super().__init__(config)
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint      = resolved["endpoint"]
        self._metrics_path  = resolved["metrics_path"]
        self._health_path   = resolved["health_path"]
        self._models_path   = resolved["models_path"]
        self._probe_timeout = resolved["probe_timeout_s"]
        # Last queue-wait header value from inference response.
        self._last_queue_wait_ms: Optional[float] = None
        self._caps = self._discover_capabilities()

    # Colibri override: /health 200 → queue_metrics=True even without /metrics.
    def _caps_from_probes(
        self,
        has_models: bool,
        has_prometheus: bool,
        has_health: bool,
        metrics_text: str,
    ) -> ServingCapabilities:
        scope = "unavailable"
        if has_health:
            scope = "interval"
        return ServingCapabilities(
            request_execution=has_models,
            queue_metrics=has_health,
            token_metrics=False,
            kv_cache_metrics=False,
            expert_tier_metrics=False,
            prometheus_metrics=False,
            request_correlation=False,
            telemetry_scope=scope,
        )

    def _fetch_health(self) -> dict:
        """Fetch /health JSON. Returns empty dict on any failure."""
        if not self._endpoint:
            return {}
        try:
            import requests
            r = requests.get(
                f"{self._endpoint}{self._health_path}",
                timeout=self._probe_timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            logger.debug("ColibriAdapter._fetch_health: %s", exc)
        return {}

    def capabilities(self) -> ServingCapabilities:
        return self._caps

    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        return RequestMetrics(request_id=request_id)

    def get_cache_state(self) -> CacheState:
        # No KV cache telemetry from Colibri API.
        return CacheState()

    def get_expert_tier_state(self) -> ExpertTierState:
        # Deferred — web dashboard only, no stable API.
        # ExpertTierState dataclass is wired and ready.
        return ExpertTierState()

    def get_queue_state(self) -> QueueState:
        if not self._caps.queue_metrics:
            return QueueState()

        health = self._fetch_health()
        if not health:
            return QueueState()

        # /health JSON field names are best-guess from Colibri docs.
        # Will be corrected during GN100 end-to-end test against real binary.
        active    = int(health.get("running", health.get("active", 0)))
        waiting   = int(health.get("queued",  health.get("waiting", 0)))
        completed = int(health.get("completed", 0))
        rejected  = int(health.get("rejected",  0))

        logger.debug(
            "ColibriAdapter.get_queue_state: active=%d waiting=%d "
            "completed=%d rejected=%d queue_wait_ms=%s",
            active, waiting, completed, rejected, self._last_queue_wait_ms,
        )
        return QueueState(
            active=active,
            waiting=waiting,
            completed=completed,
            rejected=rejected,
            queue_wait_ms=self._last_queue_wait_ms,
        )

    def record_inference_response_headers(self, headers: dict) -> None:
        """
        Call this after each inference request with the response headers dict.
        Extracts x-colibri-queue-wait-ms for QueueState.queue_wait_ms.
        Called by EngineBackedCollector if adapter is ColibriAdapter instance.
        """
        raw = headers.get("x-colibri-queue-wait-ms")
        if raw is not None:
            try:
                self._last_queue_wait_ms = float(raw)
            except (ValueError, TypeError):
                pass

    def get_engine_info(self) -> EngineInfo:
        model_loaded = ""
        if self._endpoint:
            try:
                import requests
                r = requests.get(
                    f"{self._endpoint}{self._models_path}",
                    timeout=self._probe_timeout,
                )
                if r.status_code == 200:
                    data = r.json()
                    models = data.get("data", [])
                    if models:
                        model_loaded = models[0].get("id", "")
            except Exception:
                pass
        return EngineInfo(
            engine_name=self._name,
            engine_type=self.ENGINE_TYPE,
            version="",
            model_loaded=model_loaded,
            gpu_count=1,
            capabilities=self._caps,
        )

    def is_available(self) -> bool:
        return self._caps.request_execution
