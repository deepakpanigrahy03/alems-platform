"""
================================================================================
SGLANG ADAPTER  —  alems_plugin_sglang/adapter.py
================================================================================

PURPOSE:
    ServingEngineAdapter for SGLang local inference server (port 30000).
    Dynamic capability discovery — probes /metrics at startup.
    No /health endpoint on SGLang — queue data from /metrics only.

CONFIG RESOLUTION (three layers, highest first):
    1. experiment YAML  serving_engine.endpoint
    2. config/adapters.yaml  engines.sglang.endpoint
    3. env var  ALEMS_SGLANG_URL  (set in .alems-env)

SGLANG METRICS MAPPED:
    sglang:cache_hit_rate              → CacheState.hit_rate_aggregate
    sglang:token_usage                 → CacheState.occupancy_fraction
    sglang:num_running_reqs            → QueueState.active
    sglang:num_waiting_reqs            → QueueState.waiting
    sglang:num_requests_total          → QueueState.completed
    sglang:prefill_tokens_throughput   → TokenRateState.tokens_per_second
    sglang:decode_tokens_throughput    → extra_json

TELEMETRY SCOPE: 'interval' when /metrics responds with sglang: prefixes.

NOTE: SGLang 0.5.x metric names confirmed from upstream docs.
    cache_hit_rate and token_usage are the primary cache signals.
    No per-request attribution — B2.4 aggregate only.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import json
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


class SGLangAdapter(CapabilityDiscoveryMixin, ServingEngineAdapter):
    """
    ServingEngineAdapter for SGLang.
    Capabilities discovered dynamically at startup.
    ENGINE_TYPE = 'sglang'
    """

    ENGINE_TYPE = "sglang"

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
            logger.debug("SGLangAdapter._fetch_metrics: %s", exc)
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

        hit_rate = _get(metrics, "sglang:cache_hit_rate") or 0.0
        occupancy = _get(metrics, "sglang:token_usage") or 0.0
        kv_mem_gb = _get(metrics, "sglang:kv_cache_memory_usage_gb") or 0.0
        cached_tokens = int(_get(metrics, "sglang:cached_tokens_total") or 0)

        logger.debug(
            "SGLangAdapter.get_cache_state: hit_rate=%.4f occupancy=%.4f",
            hit_rate, occupancy,
        )
        return CacheState(
            cache_type="radix",
            capacity_tokens=int(kv_mem_gb * 1024 * 1024 * 1024),
            occupied_tokens=cached_tokens,
            occupancy_fraction=round(float(occupancy), 6),
            hit_rate_aggregate=round(float(hit_rate), 6),
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

        active    = int(_get(metrics, "sglang:num_running_reqs") or 0.0)
        waiting   = int(_get(metrics, "sglang:num_waiting_reqs") or 0.0)
        completed = int(_sum_prefix(metrics, "sglang:num_requests_total"))
        return QueueState(
            active=active,
            waiting=waiting,
            completed=completed,
            rejected=0,
            queue_wait_ms=None,
        )

    def get_extra_json(self) -> Optional[str]:
        if not self._caps.prometheus_metrics:
            return None
        try:
            m = self._fetch_metrics()
            if not m:
                return None
            extra = {
                "prefill_tokens_throughput": _get(
                    m, "sglang:prefill_tokens_throughput"
                ),
                "decode_tokens_throughput": _get(
                    m, "sglang:decode_tokens_throughput"
                ),
                "time_to_first_token_ms": _get(
                    m, "sglang:time_to_first_token_seconds"
                ),
            }
            return json.dumps({k: v for k, v in extra.items() if v is not None})
        except Exception as exc:
            logger.debug("SGLangAdapter.get_extra_json: %s", exc)
            return None

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
