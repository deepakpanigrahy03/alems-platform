"""
================================================================================
CAPABILITY DISCOVERY MIXIN  —  core/serving/discovery.py
================================================================================

PURPOSE:
    Shared _discover_capabilities() implementation used by all five adapters.
    Probes the actual endpoint set at startup.
    Sets ServingCapabilities from what responds — never from assumptions.

    Each adapter calls this from __init__ after AdapterConfig.resolve().
    Subclasses override _caps_from_probes() to add engine-specific logic
    (e.g. ColibriAdapter reads /health JSON for queue counters).

PROBE SEQUENCE:
    /v1/models          — is the engine alive?  (all engines)
    /metrics            — Prometheus text?       (vLLM, SGLang, llama-server)
    /health             — JSON health?           (Colibri, future engines)
    /prometheus/metrics — non-standard path      (TensorRT-LLM)

METRIC FAMILY DETECTION:
    kv_cache_metrics   — presence of any known kv-cache prefix in /metrics text
    queue_metrics      — presence of any known queue prefix OR /health 200
    token_metrics      — presence of any Prometheus text at all
    prometheus_metrics — /metrics OR /prometheus/metrics returned # HELP

    These are detected from actual response content, not engine type.

DEGRADATION:
    If /v1/models is unreachable: all caps False, scope='unavailable'.
    If /metrics 404: prometheus_metrics=False, kv/queue/token=False.
    Experiment still runs via RemoteAPIAdapter. No crash.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from core.serving.serving_adapter import ServingCapabilities

logger = logging.getLogger(__name__)

# Prometheus metric prefixes that confirm KV cache telemetry.
_KV_PREFIXES = [
    "vllm:kv_cache",
    "llamacpp:kv_cache",
    "sglang:token_usage",
    "sglang:cache_hit_rate",
]

# Prometheus metric prefixes that confirm queue telemetry.
_QUEUE_PREFIXES = [
    "vllm:num_requests",
    "llamacpp:requests_processing",
    "sglang:num_running",
    "sglang:num_waiting",
    "trtllm_request",
]


class CapabilityDiscoveryMixin:
    """
    Mixin providing _discover_capabilities() for ServingEngineAdapter subclasses.

    Usage in adapter __init__:
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint      = resolved["endpoint"]
        self._metrics_path  = resolved["metrics_path"]
        self._health_path   = resolved["health_path"]
        self._models_path   = resolved["models_path"]
        self._probe_timeout = resolved["probe_timeout_s"]
        self._caps = self._discover_capabilities()

    Then capabilities() just returns self._caps.
    """

    # Set by AdapterConfig.resolve() in subclass __init__ before calling
    # _discover_capabilities(). Listed here for IDE clarity only.
    _endpoint: Optional[str]
    _metrics_path: str
    _health_path: str
    _models_path: str
    _probe_timeout: float

    def _discover_capabilities(self) -> ServingCapabilities:
        """
        Probe endpoint set. Return ServingCapabilities from what responds.
        Never raises. Safe to call from __init__.
        """
        if not self._endpoint:
            logger.warning(
                "%s: no endpoint configured. Returning unavailable caps.",
                self.__class__.__name__,
            )
            return ServingCapabilities(
                request_execution=False,
                telemetry_scope="unavailable",
            )

        try:
            import requests as _requests
        except ImportError:
            logger.error(
                "%s: 'requests' not installed. Cannot probe.",
                self.__class__.__name__,
            )
            return ServingCapabilities(
                request_execution=False,
                telemetry_scope="unavailable",
            )

        models_url   = f"{self._endpoint}{self._models_path}"
        metrics_url  = f"{self._endpoint}{self._metrics_path}"
        health_url   = f"{self._endpoint}{self._health_path}"
        prom_alt_url = f"{self._endpoint}/prometheus/metrics"
        timeout      = self._probe_timeout

        has_models    = False
        has_prometheus = False
        has_health    = False
        metrics_text  = ""
        # Track which metrics URL actually worked, so parse methods use it.
        self._active_metrics_url: Optional[str] = None

        # --- Probe 1: /v1/models — engine alive? ---
        try:
            r = _requests.get(models_url, timeout=timeout)
            has_models = (r.status_code == 200)
        except Exception as exc:
            logger.warning(
                "%s: %s unreachable (%s). Degrading gracefully.",
                self.__class__.__name__, models_url, exc,
            )
            return ServingCapabilities(
                request_execution=False,
                telemetry_scope="unavailable",
            )

        if not has_models:
            logger.warning(
                "%s: %s returned HTTP %d. Degrading gracefully.",
                self.__class__.__name__, models_url,
                r.status_code,
            )
            return ServingCapabilities(
                request_execution=False,
                telemetry_scope="unavailable",
            )

        # --- Probe 2: configured metrics path ---
        try:
            r = _requests.get(metrics_url, timeout=timeout)
            if r.status_code == 200 and "# HELP" in r.text:
                has_prometheus = True
                metrics_text   = r.text
                self._active_metrics_url = metrics_url
                logger.debug(
                    "%s: Prometheus metrics at %s (%d chars)",
                    self.__class__.__name__, metrics_url, len(metrics_text),
                )
        except Exception as exc:
            logger.debug("%s: metrics probe failed — %s", self.__class__.__name__, exc)

        # --- Probe 3: alternate /prometheus/metrics (TRT-LLM) ---
        if not has_prometheus and self._metrics_path != "/prometheus/metrics":
            try:
                r = _requests.get(prom_alt_url, timeout=timeout)
                if r.status_code == 200 and "# HELP" in r.text:
                    has_prometheus = True
                    metrics_text   = r.text
                    self._active_metrics_url = prom_alt_url
                    logger.debug(
                        "%s: Prometheus metrics at alt path %s",
                        self.__class__.__name__, prom_alt_url,
                    )
            except Exception as exc:
                logger.debug(
                    "%s: alt metrics probe failed — %s",
                    self.__class__.__name__, exc,
                )

        # --- Probe 4: /health ---
        try:
            r = _requests.get(health_url, timeout=timeout)
            has_health = (r.status_code == 200)
            logger.debug(
                "%s: /health → HTTP %d", self.__class__.__name__, r.status_code,
            )
        except Exception as exc:
            logger.debug("%s: health probe failed — %s", self.__class__.__name__, exc)

        caps = self._caps_from_probes(
            has_models=has_models,
            has_prometheus=has_prometheus,
            has_health=has_health,
            metrics_text=metrics_text,
        )

        logger.info(
            "%s: discovered caps — scope=%s kv=%s queue=%s prom=%s health=%s",
            self.__class__.__name__,
            caps.telemetry_scope,
            caps.kv_cache_metrics,
            caps.queue_metrics,
            caps.prometheus_metrics,
            has_health,
        )
        return caps

    def _caps_from_probes(
        self,
        has_models: bool,
        has_prometheus: bool,
        has_health: bool,
        metrics_text: str,
    ) -> ServingCapabilities:
        """
        Derive ServingCapabilities from probe results.
        Subclasses override for engine-specific logic.
        Base implementation is content-driven: reads metric family prefixes
        in the actual Prometheus response — no engine-type assumptions.
        """
        has_kv = has_prometheus and any(
            p in metrics_text for p in _KV_PREFIXES
        )
        has_queue = (has_prometheus and any(
            p in metrics_text for p in _QUEUE_PREFIXES
        )) or has_health

        scope = "unavailable"
        if has_prometheus:
            scope = "interval"

        return ServingCapabilities(
            request_execution=has_models,
            queue_metrics=has_queue,
            token_metrics=has_prometheus,
            kv_cache_metrics=has_kv,
            expert_tier_metrics=False,
            prometheus_metrics=has_prometheus,
            request_correlation=False,
            telemetry_scope=scope,
        )
