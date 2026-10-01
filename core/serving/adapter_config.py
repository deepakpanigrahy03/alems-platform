"""
core/serving/adapter_config.py: runtime side of serving endpoint resolution.

Precedence (highest first):
    1. profile serving_engine: block   (per run, passed as config dict)
    2. config/adapters.yaml            (fleet defaults, engine template)
    3. ALEMS_<ENGINE_TYPE_UPPER>_ENGINE_URL environment variable

Since 39.5.1a the merge rule is the SDK function
alems_sdk.serving.config.resolve_endpoint (pure, no I/O). This module keeps
the runtime duties: loading the fleet defaults file and reading the process
environment. The runtime registry resolves once and passes the result to the
adapter (configuration precedence belongs to the runtime, C-ORIGIN).
AdapterConfig.resolve keeps its historical dict output for existing callers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from alems_sdk.serving.config import ServingEndpoint, resolve_endpoint

logger = logging.getLogger(__name__)

# config/adapters.yaml relative to this file: core/serving -> engine root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTERS_YAML = _REPO_ROOT / "config" / "adapters.yaml"

# Kept for callers of _env_url; the SDK uses the same template.
_ENV_VAR_TEMPLATE = "ALEMS_{}_ENGINE_URL"


def _load_fleet_defaults(engine_type: str) -> dict:
    """
    Load fleet defaults for engine_type from config/adapters.yaml.

    Returns an empty dict when the file or section is missing. Never raises.
    """
    try:
        import yaml  # runtime dependency of alems-platform
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
        logger.warning("AdapterConfig: failed to load adapters.yaml: %s", exc)
        return {}


def _env_url(engine_type: str) -> Optional[str]:
    """Read ALEMS_<ENGINE_TYPE_UPPER>_ENGINE_URL; None if unset."""
    return os.environ.get(_ENV_VAR_TEMPLATE.format(engine_type.upper()))


class AdapterConfig:
    """Runtime resolver: loads the layers, delegates the merge to the SDK."""

    @staticmethod
    def resolve_endpoint(experiment_cfg: dict, engine_type: str) -> ServingEndpoint:
        """
        Resolve all layers into a ServingEndpoint.

        Args:
            experiment_cfg: the serving_engine: {...} block (may be empty).
            engine_type: ENGINE_TYPE string, e.g. 'vllm'.

        Returns:
            ServingEndpoint (endpoint None when no layer supplies one).
        """
        return resolve_endpoint(
            experiment_cfg,
            engine_type,
            fleet_defaults=_load_fleet_defaults(engine_type),
            env=os.environ,
        )

    @staticmethod
    def resolve(experiment_cfg: dict, engine_type: str) -> Dict[str, Any]:
        """
        Historical dict form of resolve_endpoint (keys unchanged).

        Keys: endpoint, metrics_path, health_path, models_path,
        probe_timeout_s, request_level_correlation, extra.
        """
        return AdapterConfig.resolve_endpoint(experiment_cfg, engine_type).as_dict()
