"""
alems_sdk.serving.config: endpoint configuration type and pure resolution.

The precedence rule (experiment block over fleet defaults over environment)
is part of the serving contract. Reading the fleet defaults file is runtime
work (machine and engine configuration, C-ORIGIN) and stays in core; core
passes the loaded defaults in. This module performs no file I/O.

The logic is a verbatim move of core AdapterConfig.resolve (pre 39.5.1a), so
resolved values are identical for identical inputs (Rule S).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Environment variable per engine type, e.g. vllm -> ALEMS_VLLM_ENGINE_URL.
_ENV_VAR_TEMPLATE = "ALEMS_{}_ENGINE_URL"

# Keys consumed by resolution; everything else in the block is passed as extra.
_KNOWN_KEYS = frozenset({
    "endpoint", "metrics_path", "health_path", "models_path",
    "probe_timeout_s", "request_level_correlation", "name", "type",
})


@dataclass(frozen=True)
class ServingEndpoint:
    """
    Fully resolved endpoint configuration for one serving adapter.

    endpoint is None when no layer supplies it; the adapter must then degrade
    (is_available False) and the runtime falls back to the remote API adapter.
    """

    endpoint: Optional[str]
    metrics_path: str = "/metrics"
    health_path: str = "/health"
    models_path: str = "/v1/models"
    probe_timeout_s: float = 3.0
    request_level_correlation: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """
        Return the dict shape historically returned by AdapterConfig.resolve.

        Returns:
            dict with keys endpoint, metrics_path, health_path, models_path,
            probe_timeout_s, request_level_correlation, extra (a copy).
        """
        return {
            "endpoint": self.endpoint,
            "metrics_path": self.metrics_path,
            "health_path": self.health_path,
            "models_path": self.models_path,
            "probe_timeout_s": self.probe_timeout_s,
            "request_level_correlation": self.request_level_correlation,
            "extra": dict(self.extra),  # copy: the dataclass stays immutable
        }


def resolve_endpoint(
    experiment_cfg: Mapping[str, Any],
    engine_type: str,
    fleet_defaults: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> ServingEndpoint:
    """
    Merge the three configuration layers into a ServingEndpoint.

    Precedence, highest first: experiment block, fleet defaults, environment.

    Args:
        experiment_cfg: the serving_engine: {...} block (may be empty).
        engine_type: ENGINE_TYPE string, e.g. 'vllm'.
        fleet_defaults: section for engine_type from the runtime's fleet
            defaults, already loaded; None or empty when unavailable.
        env: environment mapping; None means os.environ.

    Returns:
        ServingEndpoint; endpoint is None when no layer supplies one.
    """
    environ = os.environ if env is None else env
    env_url = environ.get(_ENV_VAR_TEMPLATE.format(engine_type.upper()))
    fleet = dict(fleet_defaults or {})
    exp = dict(experiment_cfg)

    # Endpoint: experiment > fleet > env (expression kept as in the original).
    endpoint = (
        exp.get("endpoint")
        or (fleet.get("endpoint") if fleet.get("endpoint") is not None else None)
        or env_url
    )
    if endpoint is None:
        # Message kept verbatim from the original resolver.
        logger.warning(
            "AdapterConfig[%s]: no endpoint in experiment YAML, "
            "adapters.yaml, or ALEMS_%s_URL env var. "
            "Adapter will degrade to RemoteAPIAdapter.",
            engine_type, engine_type.upper(),
        )

    # Paths and options: experiment > fleet > built in default.
    metrics_path = exp.get("metrics_path") or fleet.get("metrics_path") or "/metrics"
    health_path = exp.get("health_path") or fleet.get("health_path") or "/health"
    models_path = exp.get("models_path") or fleet.get("models_path") or "/v1/models"
    probe_timeout_s = float(
        exp.get("probe_timeout_s") or fleet.get("probe_timeout_s") or 3.0
    )
    request_level = bool(
        exp.get("request_level_correlation")
        or fleet.get("request_level_correlation")
        or False
    )
    extra = {k: v for k, v in exp.items() if k not in _KNOWN_KEYS}

    logger.debug(
        "AdapterConfig[%s]: resolved endpoint=%s metrics=%s health=%s "
        "models=%s timeout=%.1fs",
        engine_type, endpoint, metrics_path, health_path,
        models_path, probe_timeout_s,
    )
    return ServingEndpoint(
        endpoint=endpoint,
        metrics_path=metrics_path,
        health_path=health_path,
        models_path=models_path,
        probe_timeout_s=probe_timeout_s,
        request_level_correlation=request_level,
        extra=extra,
    )


__all__ = ["ServingEndpoint", "resolve_endpoint"]
