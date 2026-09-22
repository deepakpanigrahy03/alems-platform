"""
================================================================================
ADAPTER CONFIG RESOLVER  —  core/serving/adapter_config.py
================================================================================

PURPOSE:
    Three-layer config resolution for serving engine adapters.
    No adapter ever hardcodes an endpoint, path, or capability assumption.

RESOLUTION ORDER (highest priority first):
    1. Experiment YAML serving_engine: block   (per-run, passed as config dict)
    2. config/adapters.yaml                    (fleet defaults, this repo)
    3. Env vars  ALEMS_<ENGINE_TYPE_UPPER>_URL (.alems-env, per-machine)

    If all three layers yield None for 'endpoint', the adapter degrades
    gracefully: is_available() returns False, ServingEngineRegistry falls
    back to RemoteAPIAdapter. The experiment still runs.

USAGE (in every adapter __init__):
    from core.serving.adapter_config import AdapterConfig

    def __init__(self, config: dict):
        super().__init__(config)
        resolved = AdapterConfig.resolve(config, self.ENGINE_TYPE)
        self._endpoint        = resolved["endpoint"]          # may be None
        self._metrics_path    = resolved["metrics_path"]      # e.g. /metrics
        self._health_path     = resolved["health_path"]       # e.g. /health
        self._models_path     = resolved["models_path"]       # /v1/models
        self._probe_timeout   = resolved["probe_timeout_s"]
        self._caps = self._discover_capabilities()

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Locate config/adapters.yaml relative to this file:
#   core/serving/adapter_config.py  →  ../../config/adapters.yaml
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTERS_YAML = _REPO_ROOT / "config" / "adapters.yaml"

# Env var template per engine type.
# e.g. engine_type='vllm'  → ALEMS_VLLM_URL
# e.g. engine_type='llama_cpp' → ALEMS_LLAMA_CPP_URL
_ENV_VAR_TEMPLATE = "ALEMS_{}_ENGINE_URL"


def _load_fleet_defaults(engine_type: str) -> dict:
    """
    Load the fleet-level defaults for engine_type from config/adapters.yaml.
    Returns empty dict if the file or section is missing.
    Never raises.
    """
    try:
        import yaml  # PyYAML — already a transitive dep via alems-platform
        with open(_ADAPTERS_YAML) as fh:
            data = yaml.safe_load(fh) or {}
        engines = data.get("engines", {})
        return dict(engines.get(engine_type, {}))
    except FileNotFoundError:
        logger.debug(
            "AdapterConfig: %s not found. Using env/YAML only.", _ADAPTERS_YAML
        )
        return {}
    except Exception as exc:
        logger.warning("AdapterConfig: failed to load adapters.yaml — %s", exc)
        return {}


def _env_url(engine_type: str) -> Optional[str]:
    """
    Read ALEMS_<ENGINE_TYPE_UPPER>_URL from environment.
    engine_type 'llama_cpp' → ALEMS_LLAMA_CPP_URL
    Returns None if not set.
    """
    key = _ENV_VAR_TEMPLATE.format(engine_type.upper())
    return os.environ.get(key)


class AdapterConfig:
    """
    Static helper — call AdapterConfig.resolve(config, engine_type) in __init__.
    """

    # Sentinel: caller must supply endpoint via env or YAML.
    _MISSING = object()

    @staticmethod
    def resolve(experiment_cfg: dict, engine_type: str) -> dict[str, Any]:
        """
        Merge three config layers and return a fully resolved dict.

        Keys always present in the returned dict:
            endpoint          str | None
            metrics_path      str
            health_path       str
            models_path       str
            probe_timeout_s   float
            request_level_correlation  bool
            extra             dict  (all remaining keys from experiment_cfg)

        'endpoint' is None when no layer supplies it — adapter must degrade.

        Args:
            experiment_cfg: the serving_engine: {...} dict from experiment YAML.
                            May be empty dict for fleet-default-only runs.
            engine_type:    ENGINE_TYPE string, e.g. 'vllm', 'sglang'.
        """
        # Layer 3 (lowest): env var
        env_url = _env_url(engine_type)

        # Layer 2: fleet defaults from adapters.yaml
        fleet = _load_fleet_defaults(engine_type)

        # Layer 1 (highest): experiment YAML block
        exp = dict(experiment_cfg)

        # Resolve endpoint: experiment > fleet > env
        endpoint = (
            exp.get("endpoint")
            or (fleet.get("endpoint") if fleet.get("endpoint") is not None else None)
            or env_url
        )

        if endpoint is None:
            logger.warning(
                "AdapterConfig[%s]: no endpoint in experiment YAML, "
                "adapters.yaml, or ALEMS_%s_URL env var. "
                "Adapter will degrade to RemoteAPIAdapter.",
                engine_type, engine_type.upper(),
            )

        # Path overrides: experiment > fleet > built-in
        metrics_path = (
            exp.get("metrics_path")
            or fleet.get("metrics_path")
            or "/metrics"
        )
        health_path = (
            exp.get("health_path")
            or fleet.get("health_path")
            or "/health"
        )
        models_path = (
            exp.get("models_path")
            or fleet.get("models_path")
            or "/v1/models"
        )
        probe_timeout_s = float(
            exp.get("probe_timeout_s")
            or fleet.get("probe_timeout_s")
            or 3.0
        )
        request_level = bool(
            exp.get("request_level_correlation")
            or fleet.get("request_level_correlation")
            or False
        )

        # Collect any extra keys from experiment YAML for adapter-specific use
        known = {
            "endpoint", "metrics_path", "health_path", "models_path",
            "probe_timeout_s", "request_level_correlation", "name", "type",
        }
        extra = {k: v for k, v in exp.items() if k not in known}

        resolved = {
            "endpoint": endpoint,
            "metrics_path": metrics_path,
            "health_path": health_path,
            "models_path": models_path,
            "probe_timeout_s": probe_timeout_s,
            "request_level_correlation": request_level,
            "extra": extra,
        }

        logger.debug(
            "AdapterConfig[%s]: resolved endpoint=%s metrics=%s health=%s "
            "models=%s timeout=%.1fs",
            engine_type, endpoint, metrics_path, health_path,
            models_path, probe_timeout_s,
        )
        return resolved
