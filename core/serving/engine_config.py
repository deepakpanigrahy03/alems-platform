"""
engine_config.py: resolved serving engine configuration for a local provider.

Why this exists:
    Core code must not know engine names, ports, process names or paths.
    Every consumer (engine instance match, KV snapshot sampler, restart helper)
    asks this module and gets one resolved view, built from the same layers
    the platform uses everywhere:

      1. plugin       ServingEngineAdapter subclass PROCESS_TRAITS
                      (worker process pattern, single process); lowest
      2. models.yaml  provider block: is_local, base_url, serving_engine:
                      {type, endpoint, serve_script, serve_args, any trait}
      3. environment  ~/.alemsrc (loaded into os.environ, shell wins):
                      ALEMS_<TYPE>_ENGINE_URL, ALEMS_SERVE_LOG_DIR,
                      variables used inside serve_script ($ALEMS_AI_STACK)

    Endpoint precedence is the SDK resolver's (alems_sdk.serving.config):
    serving_engine.endpoint > provider base_url > ALEMS_<TYPE>_ENGINE_URL.

Usage from shell (restart helper): python -m core.serving.engine_config PROVIDER
prints the resolved view as JSON, or exits 1 when the provider is not a
configured local serving engine.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

PROVENANCE_DIR_ENV = "ALEMS_SERVE_LOG_DIR"
_CACHE = {}  # type: Dict[str, Optional[Dict[str, Any]]]


def _providers():
    # type: () -> Dict[str, Any]
    """Provider blocks after fragment merge (models.yaml wins), env loaded."""
    try:
        from core.storage.alemsrc import load_alemsrc
        load_alemsrc()   # setdefault: shell values stay authoritative
        from core import models_loader
        return models_loader._load()["providers"]
    except Exception as exc:  # config unreadable: no engine config, logged
        logger.warning("engine config: models config unreadable: %s", exc)
        return {}


def _meta(block):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Provider metadata, whether or not the loader nests it."""
    return block.get("provider_meta") or block


def _strip_api(base_url):
    # type: (Optional[str]) -> Optional[str]
    """Server root from an OpenAI style base_url (drops the /v1 path)."""
    if not base_url:
        return None
    url = urlparse(os.path.expandvars(base_url))
    return "%s://%s" % (url.scheme, url.netloc) if url.netloc else None


def _plugin_traits(cfg):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """PROCESS_TRAITS declared by the engine's plugin adapter class."""
    try:
        from core.serving.registry import ServingEngineRegistry
        ServingEngineRegistry.discover()
        cls = ServingEngineRegistry.get_all().get(cfg.get("type"))
        if cls is None:
            logger.warning("engine config: no plugin registered for engine type %s", cfg.get("type"))
            return {}
        return dict(getattr(cls, "PROCESS_TRAITS", {}) or {})
    except Exception as exc:  # plugin absent: no traits, logged
        logger.warning("engine config: no plugin traits for %s: %s", cfg.get("type"), exc)
        return {}


def resolve(provider):
    # type: (Optional[str]) -> Optional[Dict[str, Any]]
    """
    Resolved view of one provider, or None when it is not a local serving engine.

    Returns:
        dict: provider, type, endpoint, port, worker_process_pattern,
        single_process, provenance_log_dir, serve_script, serve_args.
    """
    if not provider:
        return None
    if provider not in _CACHE:
        _CACHE[provider] = _resolve(provider, _providers().get(provider) or {})
    return _CACHE[provider]


def _resolve(provider, block):
    # type: (str, Dict[str, Any]) -> Optional[Dict[str, Any]]
    """Merge plugin, models.yaml and environment layers for one provider."""
    meta = _meta(block)
    se = meta.get("serving_engine")
    if not meta.get("is_local") or not isinstance(se, dict) or not se.get("type"):
        return None
    from alems_sdk.serving.config import resolve_endpoint
    ep = resolve_endpoint(se, se["type"], {"endpoint": _strip_api(meta.get("base_url"))})
    out = _plugin_traits({"type": se["type"], "endpoint": ep.endpoint})
    out.update(ep.extra)   # models.yaml keys override plugin traits
    port = urlparse(ep.endpoint).port if ep.endpoint else None
    out.update({
        "provider": provider, "type": se["type"], "endpoint": ep.endpoint, "port": port,
        "provenance_log_dir": os.environ.get(PROVENANCE_DIR_ENV) or out.get("provenance_log_dir"),
        "serve_script": os.path.expandvars(out["serve_script"]) if out.get("serve_script") else None,
    })
    return out


def local_engines():
    # type: () -> List[Dict[str, Any]]
    """Resolved view of every configured local serving engine."""
    return [cfg for cfg in (resolve(p) for p in sorted(_providers())) if cfg]


def main(argv):
    # type: (List[str]) -> int
    """Print one provider's resolved view as JSON (for shell helpers)."""
    cfg = resolve(argv[0] if argv else None)
    if not cfg:
        logger.error("not a configured local serving engine: %s", argv[:1])
        return 1
    sys.stdout.write(json.dumps(cfg) + "\n")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main(sys.argv[1:]))
