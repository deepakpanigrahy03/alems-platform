"""
================================================================================
TENSORRT-LLM ADAPTER  —  alems_plugin_tensorrt_llm/adapter.py
================================================================================

PURPOSE:
    ServingEngineAdapter for TensorRT-LLM inference server.
    Non-standard Prometheus path: /prometheus/metrics (not /metrics).
    Not testable until TRT-LLM installation available on GN100.

CONFIG RESOLUTION (three layers, highest first):
    1. experiment YAML  serving_engine.endpoint
    2. config/adapters.yaml  engines.tensorrt_llm.endpoint
    3. env var  ALEMS_TENSORRT_LLM_URL  (set in .alems-env)

ENDPOINT REALITY (TRT-LLM server):
    /v1/models            → 200  (engine alive)
    /prometheus/metrics   → 200  Prometheus text with trtllm_ prefixed metrics
    /metrics              → 404  (non-standard path — discovery probes alt)
    /health               → may or may not respond (not confirmed)

TRTLLM METRICS EXPECTED:
    trtllm_request_success_total       → QueueState.completed
    trtllm_request_active_total        → QueueState.active
    trtllm_request_waiting_total       → QueueState.waiting
    trtllm_kv_cache_fraction           → CacheState.occupancy_fraction
    trtllm_tokens_per_second           → TokenRateState.tokens_per_second

    All names are best-guess from TRT-LLM docs.
    Will be corrected during first live test.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
import re
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


def _parse_prometheus(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(
            r'^([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?)\s+([\deE+\-.]+)$',
            line,
        )
        if m:
            try:
                result[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return result


def _get(metrics: dict, prefix: str) -> Optional[float]:
    for k, v in metrics.items():
        if k.startswith(prefix):
            return v
    return None


def _sum_prefix(metrics: dict, prefix: str) -> float:
    return sum(v for k, v in metrics.items() if k.startswith(prefix))


class TensorRTLLMAdapter(CapabilityDiscoveryMixin, ServingEngineAdapter):
    """
    ServingEngineAdapter for TensorRT-LLM.
    Probes /prometheus/metrics (non-standard path).
    Not yet tested — will be corrected on first live GN100 run.
    ENGINE_TYPE = 'tensorrt_llm'
    """

    ENGINE_TYPE = "tensorrt_llm"

    def __init__(self, config: dict):
        super().__init__(config)
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint      = resolved["endpoint"]
        # TRT-LLM: fleet default sets metrics_path=/prometheus/metrics
        self._metrics_path  = resolved["metrics_path"]
        self._health_path   = resolved["health_path"]
        self._models_path   = resolved["models_path"]
        self._probe_timeout = resolved["probe_timeout_s"]
        self._caps = self._discover_capabilities()

    def _fetch_metrics(self) -> dict[str, float]:
        if not self._active_metrics_url:
            return {}
        try:
            import requests
            r = requests.get(self._active_metrics_url, timeout=self._probe_timeout)
            if r.status_code == 200:
                return _parse_prometheus(r.text)
        except Exception as exc:
            logger.debug("TensorRTLLMAdapter._fetch_metrics: %s", exc)
        return {}

    def capabilities(self) -> ServingCapabilities:
        return self._caps

    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        return RequestMetrics(request_id=request_id)

    def get_cache_state(self) -> CacheState:
        if not self._caps.kv_cache_metrics:
            return CacheState()

        metrics = self._fetch_metrics()
        if not metrics:
            return CacheState()

        occupancy = _get(metrics, "trtllm_kv_cache_fraction") or 0.0
        return CacheState(
            cache_type="kv",
            capacity_tokens=0,
            occupied_tokens=0,
            occupancy_fraction=round(float(occupancy), 6),
            hit_rate_aggregate=0.0,
            num_evictions=0,
        )

    def get_expert_tier_state(self) -> ExpertTierState:
        return ExpertTierState()

    def get_queue_state(self) -> QueueState:
        if not self._caps.queue_metrics:
            return QueueState()

        metrics = self._fetch_metrics()
        if not metrics:
            return QueueState()

        active    = int(_get(metrics, "trtllm_request_active_total") or 0.0)
        waiting   = int(_get(metrics, "trtllm_request_waiting_total") or 0.0)
        completed = int(_sum_prefix(metrics, "trtllm_request_success_total"))
        return QueueState(
            active=active,
            waiting=waiting,
            completed=completed,
            rejected=0,
            queue_wait_ms=None,
        )

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
