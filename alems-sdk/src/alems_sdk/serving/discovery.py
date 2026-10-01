"""
alems_sdk.serving.discovery: shared capability discovery for serving adapters.

Verbatim move of core/serving/discovery.py (pre 39.5.1a); core re-exports it.
Capabilities are derived from what the endpoints actually return, never from
the engine type.

Probe sequence:
    models path          engine alive?            (all engines)
    metrics path         Prometheus text?         (vLLM, SGLang, llama-server)
    health path          JSON health?             (Colibri, future engines)
    /prometheus/metrics  non standard path        (TensorRT-LLM)

requests is imported lazily and is declared by the extra alems-sdk[serving];
without it the adapter degrades to unavailable capabilities and never raises.
"""

from __future__ import annotations

import logging
from typing import Optional

from alems_sdk.serving import ServingCapabilities

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

    The subclass sets _endpoint, _metrics_path, _health_path, _models_path and
    _probe_timeout (from its ServingEndpoint) before calling
    _discover_capabilities(); capabilities() then returns the stored result.
    """

    # Set by the subclass __init__ before _discover_capabilities() runs.
    _endpoint: Optional[str]
    _metrics_path: str
    _health_path: str
    _models_path: str
    _probe_timeout: float

    def _discover_capabilities(self) -> ServingCapabilities:
        """
        Probe the endpoint set and return capabilities from what responds.

        Never raises; safe to call from __init__.
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
            import requests as _requests  # optional extra alems-sdk[serving]
        except ImportError:
            logger.error(
                "%s: 'requests' not installed. Cannot probe.",
                self.__class__.__name__,
            )
            return ServingCapabilities(
                request_execution=False,
                telemetry_scope="unavailable",
            )

        models_url = f"{self._endpoint}{self._models_path}"
        metrics_url = f"{self._endpoint}{self._metrics_path}"
        health_url = f"{self._endpoint}{self._health_path}"
        prom_alt_url = f"{self._endpoint}/prometheus/metrics"
        timeout = self._probe_timeout

        has_models = False
        has_prometheus = False
        has_health = False
        metrics_text = ""
        # Which metrics URL actually worked, so parse methods reuse it.
        self._active_metrics_url: Optional[str] = None

        # Probe 1: models path, is the engine alive?
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

        # Probe 2: configured metrics path.
        try:
            r = _requests.get(metrics_url, timeout=timeout)
            if r.status_code == 200 and "# HELP" in r.text:
                has_prometheus = True
                metrics_text = r.text
                self._active_metrics_url = metrics_url
                logger.debug(
                    "%s: Prometheus metrics at %s (%d chars)",
                    self.__class__.__name__, metrics_url, len(metrics_text),
                )
        except Exception as exc:
            logger.debug("%s: metrics probe failed: %s", self.__class__.__name__, exc)

        # Probe 3: alternate /prometheus/metrics (TensorRT-LLM).
        if not has_prometheus and self._metrics_path != "/prometheus/metrics":
            try:
                r = _requests.get(prom_alt_url, timeout=timeout)
                if r.status_code == 200 and "# HELP" in r.text:
                    has_prometheus = True
                    metrics_text = r.text
                    self._active_metrics_url = prom_alt_url
                    logger.debug(
                        "%s: Prometheus metrics at alt path %s",
                        self.__class__.__name__, prom_alt_url,
                    )
            except Exception as exc:
                logger.debug(
                    "%s: alt metrics probe failed: %s",
                    self.__class__.__name__, exc,
                )

        # Probe 4: health path.
        try:
            r = _requests.get(health_url, timeout=timeout)
            has_health = (r.status_code == 200)
            logger.debug(
                "%s: /health -> HTTP %d", self.__class__.__name__, r.status_code,
            )
        except Exception as exc:
            logger.debug("%s: health probe failed: %s", self.__class__.__name__, exc)

        caps = self._caps_from_probes(
            has_models=has_models,
            has_prometheus=has_prometheus,
            has_health=has_health,
            metrics_text=metrics_text,
        )

        logger.info(
            "%s: discovered caps: scope=%s kv=%s queue=%s prom=%s health=%s",
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
        Derive capabilities from probe results (pure; no I/O).

        Content driven: reads metric family prefixes in the actual response.
        Subclasses override for engine specific logic.
        """
        has_kv = has_prometheus and any(p in metrics_text for p in _KV_PREFIXES)
        has_queue = (
            has_prometheus and any(p in metrics_text for p in _QUEUE_PREFIXES)
        ) or has_health

        # Prometheus deltas give interval scope; nothing finer is claimed here.
        scope = "interval" if has_prometheus else "unavailable"

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


__all__ = ["CapabilityDiscoveryMixin", "_KV_PREFIXES", "_QUEUE_PREFIXES"]
