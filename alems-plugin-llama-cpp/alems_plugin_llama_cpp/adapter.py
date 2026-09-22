"""
================================================================================
LLAMA-CPP ADAPTER  —  alems_plugin_llama_cpp/adapter.py
================================================================================

PURPOSE:
    ServingEngineAdapter for llama-cpp-python server.
    Capability-limited by design — discovery is honest about what responds.

CONFIG RESOLUTION (three layers, highest first):
    1. experiment YAML  serving_engine.endpoint
    2. config/adapters.yaml  engines.llama_cpp.endpoint
    3. env var  ALEMS_LLAMA_CPP_URL  (set in .alems-env)

ENDPOINT REALITY (llama-cpp-python server):
    /v1/models          → 200  (engine alive check)
    /v1/chat/completions → 200  (inference)
    /metrics            → 404  (NOT PRESENT)
    /health             → 404  (NOT PRESENT)

    Discovery result: telemetry_scope='unavailable', all telemetry caps False.
    EngineBackedCollector writes zero rows to serving_runtime_snapshots.
    Experiment continues normally via RemoteAPIAdapter inference path.

UPGRADE PATH (C++ llama-server binary with --metrics flag):
    The C++ llama-server binary exposes Prometheus metrics at /metrics
    with 'llamacpp:' prefixed metric families:
        llamacpp:kv_cache_usage_fraction
        llamacpp:kv_cache_tokens_total
        llamacpp:requests_processing
        llamacpp:tokens_per_second
    When llama-server replaces llama-cpp-python in serve_llamacpp.sh,
    discovery will automatically detect /metrics and elevate all caps.
    No adapter code changes required.

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


class LlamaCppAdapter(CapabilityDiscoveryMixin, ServingEngineAdapter):
    """
    ServingEngineAdapter for llama-cpp-python server.
    With llama-cpp-python: telemetry_scope='unavailable' (no /metrics, no /health).
    With C++ llama-server --metrics: auto-upgrades to full Prometheus caps.
    ENGINE_TYPE = 'llama_cpp'
    """

    ENGINE_TYPE = "llama_cpp"

    def __init__(self, config: dict):
        super().__init__(config)
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint      = resolved["endpoint"]
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
            logger.debug("LlamaCppAdapter._fetch_metrics: %s", exc)
        return {}

    def capabilities(self) -> ServingCapabilities:
        return self._caps

    def get_request_metrics(
        self, request_id: Optional[str] = None
    ) -> RequestMetrics:
        return RequestMetrics(request_id=request_id)

    def get_cache_state(self) -> CacheState:
        # Unavailable with llama-cpp-python. Auto-active with llama-server binary.
        if not self._caps.kv_cache_metrics:
            return CacheState()

        metrics = self._fetch_metrics()
        if not metrics:
            return CacheState()

        occupancy = _get(metrics, "llamacpp:kv_cache_usage_fraction") or 0.0
        capacity  = int(_get(metrics, "llamacpp:kv_cache_tokens_total") or 0)
        occupied  = int(capacity * occupancy)
        return CacheState(
            cache_type="kv",
            capacity_tokens=capacity,
            occupied_tokens=occupied,
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

        active = int(_get(metrics, "llamacpp:requests_processing") or 0.0)
        return QueueState(active=active)

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
            gpu_count=0,
            capabilities=self._caps,
        )

    def is_available(self) -> bool:
        return self._caps.request_execution
