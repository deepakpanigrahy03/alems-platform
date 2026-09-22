"""
================================================================================
VLLM ADAPTER  —  alems_plugin_vllm/adapter.py
================================================================================

PURPOSE:
    ServingEngineAdapter for vLLM local inference server.
    Dynamic capability discovery — probes endpoints at startup.
    Parse logic verified against GN100 Mistral-7B-Instruct-v0.3, preserved.

CONFIG RESOLUTION (three layers, highest first):
    1. experiment YAML  serving_engine.endpoint
    2. config/adapters.yaml  engines.vllm.endpoint
    3. env var  ALEMS_VLLM_URL  (set in .alems-env)

VERIFIED AGAINST: Mistral-7B-Instruct-v0.3 on GN100 (nvidia_grace, aarch64)
    Discovered at startup: /metrics → has_prometheus=True → kv+queue caps set.
    No /health endpoint on vLLM — queue data comes from /metrics only.

METRICS MAPPED:
    vllm:kv_cache_usage_perc           → CacheState.occupancy_fraction
    vllm:prefix_cache_hits_total       → hit rate numerator
    vllm:prefix_cache_queries_total    → hit rate denominator
    vllm:num_preemptions_total         → CacheState.num_evictions
    vllm:cache_config_info             → capacity_tokens (num_gpu_blocks*block_size)
    vllm:num_requests_running          → QueueState.active
    vllm:num_requests_waiting          → QueueState.waiting
    vllm:request_success_total         → QueueState.completed
    vllm:prompt_tokens_total           → TokenRateState.prompt_tokens_total
    vllm:generation_tokens_total       → TokenRateState.generation_tokens_total
    vllm:prompt_tokens_by_source_total → extra_json (cache/compute breakdown)
    vllm:prompt_tokens_cached_total    → extra_json

TELEMETRY SCOPE: 'interval' (discovered, not assumed)

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


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus text parser utilities  (preserved exactly from B3)
# ─────────────────────────────────────────────────────────────────────────────

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


def _label_value(
    metrics: dict, prefix: str, label: str, value: str
) -> Optional[float]:
    pattern = f'{label}="{value}"'
    for k, v in metrics.items():
        if k.startswith(prefix) and pattern in k:
            return v
    return None


def _cache_config(metrics: dict) -> dict:
    result = {"num_gpu_blocks": 0, "block_size": 16}
    for k in metrics:
        if k.startswith("vllm:cache_config_info"):
            for field in ("num_gpu_blocks", "block_size"):
                m = re.search(rf'{field}="(\d+)"', k)
                if m:
                    result[field] = int(m.group(1))
            break
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Adapter
# ─────────────────────────────────────────────────────────────────────────────

class VLLMAdapter(CapabilityDiscoveryMixin, ServingEngineAdapter):
    """
    ServingEngineAdapter for vLLM.
    Capabilities discovered dynamically at startup via probe sequence.
    ENGINE_TYPE = 'vllm'
    """

    ENGINE_TYPE = "vllm"

    def __init__(self, config: dict):
        super().__init__(config)
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint        = resolved["endpoint"]
        self._metrics_path    = resolved["metrics_path"]
        self._health_path     = resolved["health_path"]
        self._models_path     = resolved["models_path"]
        self._probe_timeout   = resolved["probe_timeout_s"]
        self._request_level   = resolved["request_level_correlation"]

        # Discover caps by probing — sets self._active_metrics_url too.
        self._caps = self._discover_capabilities()

        # Cache config is static — fetch once after discovery confirms /metrics.
        self._cfg: dict = {}
        if self._caps.kv_cache_metrics and self._active_metrics_url:
            self._cfg = _cache_config(self._fetch_metrics())
            logger.info(
                "VLLMAdapter: cache config — "
                "num_gpu_blocks=%d block_size=%d capacity_tokens=%d",
                self._cfg.get("num_gpu_blocks", 0),
                self._cfg.get("block_size", 16),
                self._cfg.get("num_gpu_blocks", 0) * self._cfg.get("block_size", 16),
            )

    # vLLM override: inject request_level_correlation into caps.
    def _caps_from_probes(
        self,
        has_models: bool,
        has_prometheus: bool,
        has_health: bool,
        metrics_text: str,
    ) -> ServingCapabilities:
        base = super()._caps_from_probes(
            has_models=has_models,
            has_prometheus=has_prometheus,
            has_health=has_health,
            metrics_text=metrics_text,
        )
        # Apply request_level_correlation from config if Prometheus confirmed.
        if base.prometheus_metrics and self._request_level:
            base.request_correlation = True
            base.telemetry_scope = "request"
        return base

    def _fetch_metrics(self) -> dict[str, float]:
        if not self._active_metrics_url:
            return {}
        try:
            import requests
            r = requests.get(self._active_metrics_url, timeout=self._probe_timeout)
            if r.status_code == 200:
                return _parse_prometheus(r.text)
        except Exception as exc:
            logger.debug("VLLMAdapter._fetch_metrics: %s", exc)
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

        kv_usage = _get(metrics, "vllm:kv_cache_usage_perc") or 0.0
        num_blocks = self._cfg.get("num_gpu_blocks", 0)
        block_size = self._cfg.get("block_size", 16)
        capacity_tokens = num_blocks * block_size
        occupied_tokens = int(capacity_tokens * kv_usage)
        queries = _get(metrics, "vllm:prefix_cache_queries_total") or 0.0
        hits    = _get(metrics, "vllm:prefix_cache_hits_total") or 0.0
        hit_rate = (hits / queries) if queries > 0 else 0.0
        evictions = int(_get(metrics, "vllm:num_preemptions_total") or 0.0)

        logger.debug(
            "VLLMAdapter.get_cache_state: kv_usage=%.4f capacity=%d "
            "occupied=%d hit_rate=%.4f (hits=%d/queries=%d) evictions=%d",
            kv_usage, capacity_tokens, occupied_tokens,
            hit_rate, int(hits), int(queries), evictions,
        )
        return CacheState(
            cache_type="kv+prefix",
            capacity_tokens=capacity_tokens,
            occupied_tokens=occupied_tokens,
            occupancy_fraction=round(kv_usage, 6),
            hit_rate_aggregate=round(hit_rate, 6),
            num_evictions=evictions,
        )

    def get_expert_tier_state(self) -> ExpertTierState:
        return ExpertTierState()

    def get_queue_state(self) -> QueueState:
        if not self._caps.queue_metrics:
            return QueueState()

        metrics = self._fetch_metrics()
        if not metrics:
            return QueueState()

        active    = int(_get(metrics, "vllm:num_requests_running") or 0.0)
        waiting   = int(_get(metrics, "vllm:num_requests_waiting") or 0.0)
        completed = int(_sum_prefix(metrics, "vllm:request_success_total"))
        return QueueState(
            active=active,
            waiting=waiting,
            completed=completed,
            rejected=0,
            queue_wait_ms=None,
        )

    def get_extra_json(self, metrics: Optional[dict] = None) -> Optional[str]:
        if not self._caps.prometheus_metrics:
            return None
        try:
            m = metrics or self._fetch_metrics()
            if not m:
                return None
            extra = {
                "prompt_tokens_local_compute": int(
                    _label_value(m, "vllm:prompt_tokens_by_source_total",
                                 "source", "local_compute") or 0
                ),
                "prompt_tokens_local_cache_hit": int(
                    _label_value(m, "vllm:prompt_tokens_by_source_total",
                                 "source", "local_cache_hit") or 0
                ),
                "prompt_tokens_external_kv": int(
                    _label_value(m, "vllm:prompt_tokens_by_source_total",
                                 "source", "external_kv_transfer") or 0
                ),
                "prompt_tokens_cached_total": int(
                    _get(m, "vllm:prompt_tokens_cached_total") or 0
                ),
                "prefix_caching_enabled": True,
            }
            return json.dumps(extra)
        except Exception as exc:
            logger.debug("VLLMAdapter.get_extra_json: %s", exc)
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
