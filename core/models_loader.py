"""
================================================================================
MODELS LOADER — reads models.yaml, expands defaults at runtime
================================================================================

Purpose:
    Single loader that reads the human-editable models.yaml and expands
    provider defaults + global defaults into fully resolved model configs.

    Output shape is identical to old models.json flat dicts — all consumers
    (ModelFactory, config_loader, run_single.py) see same rich structure.

No build step — expansion happens in memory at import time.
Cached after first load — zero repeated I/O.

8.6-B5 addition:
    Fragment discovery via alems.models.fragments entry points.
    Plugin fragments fill provider gaps BEFORE models.yaml values are applied.
    models.yaml always wins on collision — fragments never override researcher config.

Author: A-LEMS Chunk 7 + 8.6-B5
================================================================================
"""

import copy
import logging
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
import os

logger = logging.getLogger(__name__)

# Path to yaml — relative to project root
_YAML_PATH = Path(__file__).parent.parent / "config" / "models.yaml"

# Module-level cache — loaded once per process
_cache: Optional[Dict] = None

# Keys ending in _env that are runtime metadata, NOT resolution directives.
# api_key_env survives into the registry for use by the inference client at runtime.
_ENV_METADATA_KEYS = {"api_key_env"}


# =============================================================================
# PUBLIC API
# =============================================================================

def get_provider(provider_id: str) -> Optional[Dict]:
    """
    Get fully resolved provider config (meta + models expanded).

    Args:
        provider_id: e.g. 'groq', 'ollama_remote', 'llama_cpp'

    Returns:
        Dict with provider_meta and models list, or None if not found.
    """
    data = _load()
    return data["providers"].get(provider_id)


def get_model(provider_id: str, model_id: str) -> Optional[Dict]:
    """
    Get fully resolved flat config for one model.

    Merges: global_defaults → provider_defaults → model fields.
    Also adds provider-level meta fields (is_local, network_type, etc).
    Ready for LinearExecutor / AgenticExecutor constructors.

    Args:
        provider_id: e.g. 'groq'
        model_id:    e.g. 'llama-3.3-70b-versatile'

    Returns:
        Flat merged dict or None if not found / not available.
    """
    data = _load()
    provider_block = data["providers"].get(provider_id)
    if not provider_block:
        return None

    meta = provider_block["provider_meta"]
    model = next(
        (m for m in provider_block["models"]
         if m.get("model_id") == model_id and m.get("available", True)),
        None,
    )
    if not model:
        return None

    # Build flat dict — same shape old models.json produced
    flat = {
        "provider":            provider_id,
        "model_id":            model_id,
        "model_uid":           f"{provider_id}::{model_id}",
        # provider meta fields
        "is_local":            meta.get("is_local", False),
        "access_method":       meta.get("access_method", "api_http"),
        "network_type":        meta.get("network_type", "internet"),
        "captures_network_io": meta.get("captures_network_io", True),
        "energy_side":         meta.get("energy_side", "client_only"),
        "execution_site":           meta.get("execution_site"),
        "transport":                meta.get("transport"),
        "remote_energy_available":  meta.get("remote_energy_available", False),
        "openai_compat":       meta.get("openai_compat", False),
        "base_url":            os.path.expandvars(meta.get("base_url", "")),
        "api_key_env":         meta.get("api_key_env"),
        "env_path":            meta.get("env_path", ""),
        "cost_class":          meta.get("cost_class", "free"),
        "priority":            meta.get("priority", 99),
        "rate_limit_tpm":      meta.get("rate_limit_tpm"),
        # build api_endpoint for executor backward compat
        "api_endpoint":        _build_endpoint(meta, provider_id),
    }

    # Merge model fields (model wins on conflict)
    flat.update({k: v for k, v in model.items()
                 if k not in ("id", "file_params", "media_params", "runtime_params")})

    # Flatten nested param blocks to top level
    for param_key in ("file_params", "media_params", "runtime_params"):
        flat.update(model.get(param_key, {}))

    # model_path convenience alias for llama_cpp executor compat
    if "model_path" in model.get("file_params", {}):
        flat["model_path"] = model["file_params"]["model_path"]

    return flat


def list_providers(task: Optional[str] = None) -> Dict[str, Dict]:
    """
    List all available providers, optionally filtered by task.

    Args:
        task: e.g. 'text-generation', None = all

    Returns:
        Dict provider_id → provider block
    """
    data = _load()
    providers = data["providers"]
    if task is None:
        return providers
    # Filter providers that have at least one model supporting the task
    return {
        pid: pcfg for pid, pcfg in providers.items()
        if any(task in m.get("tasks", []) for m in pcfg.get("models", []))
    }


def list_models(provider_id: str) -> List[Dict]:
    """
    List all available models for a provider.

    Args:
        provider_id: provider key string

    Returns:
        List of expanded model dicts
    """
    data = _load()
    provider_block = data["providers"].get(provider_id, {})
    return [m for m in provider_block.get("models", []) if m.get("available", True)]


def get_backward_compat(mode: str, workflow: str) -> Optional[Dict]:
    """
    Backward compat for get_model_config(mode, workflow).

    Resolves through get_model() — same proper path as all other callers.
    _legacy_map only stores provider+model_id pointer, no duplicate config.

    Args:
        mode:     'cloud' or 'local'
        workflow: 'linear' or 'agentic'

    Returns:
        Fully resolved flat config dict or None
    """
    data = _load()
    mapping = data.get("_legacy_map", {}).get(mode, {}).get(workflow)
    if not mapping:
        return None
    # resolve through proper path — gets base_url, api_key_env, all meta fields
    result = get_model(mapping["provider"], mapping["model_id"])
    if not result:
        return None
    # apply workflow-specific overrides (max_tokens, tools_supported)
    result = dict(result)
    result.update({k: v for k, v in mapping.items()
                   if k not in ("provider", "model_id")})
    return result


# =============================================================================
# INTERNAL — load + expand
# =============================================================================

def _load() -> Dict:
    """
    Load and cache expanded models data. Reads yaml once per process.

    8.6-B5: After reading models.yaml, fragment discovery runs.
    Fragments fill provider gaps — models.yaml values always win on collision.
    Fragment env vars are resolved BEFORE merge so models.yaml beats env vars too.

    Returns:
        Dict with providers (expanded) and _backward_compat
    """
    global _cache
    if _cache is not None:
        return _cache   # early return — already loaded

    if not _YAML_PATH.exists():
        raise FileNotFoundError(f"models.yaml not found at {_YAML_PATH}")

    with open(_YAML_PATH, "r") as f:
        raw = yaml.safe_load(f)

    global_defaults = raw.get("_defaults", {})
    # raw_providers is the researcher-authored provider dict from models.yaml
    raw_providers = raw.get("providers", {})

    # -------------------------------------------------------------------------
    # 8.6-B5: Discover plugin fragments and merge into raw_providers BEFORE
    # expansion. This is the correct insertion point — fragments supply raw
    # YAML-equivalent dicts; the expansion loop below then handles them uniformly.
    # models.yaml wins: raw_providers already populated, _deep_merge_missing
    # only fills keys that are absent.
    # -------------------------------------------------------------------------
    fragments, provenance = _load_fragments()
    _resolve_fragment_env_vars(fragments)
    _deep_merge_missing(raw_providers, fragments)
    _log_provenance(raw_providers, provenance)

    expanded_providers = {}

    for provider_id, provider_raw in raw_providers.items():
        # Provider-level defaults override global defaults
        provider_defaults = {**global_defaults, **provider_raw.get("defaults", {})}

        # Build provider_meta — all scalar fields except 'models' and 'defaults'
        meta_keys = {k: v for k, v in provider_raw.items()
                     if k not in ("models", "defaults", "serving_engine_defaults")}
        meta_keys["provider_id"] = provider_id

        # serving_engine_defaults is carried through for B4 experiment resolver.
        # B5 does not apply it — B4 reads it at experiment config time.
        if "serving_engine_defaults" in provider_raw:
            meta_keys["serving_engine_defaults"] = provider_raw["serving_engine_defaults"]

        expanded_models = []
        for model_raw in provider_raw.get("models", []):
            # Expand: provider_defaults → model overrides
            model = {**provider_defaults}
            model.update({k: v for k, v in model_raw.items() if k != "id"})

            # Normalize model_id from 'id' field
            model["model_id"] = model_raw["id"]
            model["model_uid"] = f"{provider_id}::{model_raw['id']}"
            model.setdefault("available", True)

            expanded_models.append(model)

        expanded_providers[provider_id] = {
            "provider_meta": meta_keys,
            "models": expanded_models,
        }

    _cache = {
        "providers": expanded_providers,
        "_legacy_map": raw.get("_legacy_map", {}),
    }

    logger.debug("ModelsLoader: loaded %d providers from models.yaml",
                 len(expanded_providers))
    return _cache


def _build_endpoint(meta: Dict, provider_id: str) -> str:
    """
    Build api_endpoint string for executor backward compat.

    Ollama:      base_url/api/chat
    OpenAI-compat: base_url/chat/completions
    Other:       base_url

    Args:
        meta:        provider_meta dict
        provider_id: provider key

    Returns:
        str endpoint URL
    """
    base = os.path.expandvars(meta.get("base_url", "")).rstrip("/")
    if not base:
        return ""
    # ollama providers use /api/chat endpoint
    if "ollama" in provider_id:
        return f"{base}/api/chat"
    if meta.get("openai_compat", False):
        return f"{base}/chat/completions"
    return base


# =============================================================================
# 8.6-B5 — Fragment discovery and merge helpers
# =============================================================================

def _resolve_env_keys(cfg: dict) -> None:
    """
    Consume <key>_env directives in cfg in place.

    For each key ending in _env (excluding _ENV_METADATA_KEYS):
        Read env var named by that value.
        If set and base_key present, override base_key.
        Remove the _env directive key regardless.

    Logs a warning if base_key is absent (malformed fragment).
    api_key_env is never consumed — it is runtime metadata for the inference client.
    """
    env_keys = [
        k for k in list(cfg)
        if k.endswith("_env") and k not in _ENV_METADATA_KEYS
    ]
    for env_key in env_keys:
        base_key = env_key[: -len("_env")]
        env_var = cfg.pop(env_key)   # always remove the directive key
        if not env_var:
            continue
        if base_key not in cfg:
            logger.warning(
                "models_loader: fragment declares '%s' but '%s' is absent — "
                "ignoring env directive.",
                env_key, base_key,
            )
            continue
        val = os.environ.get(env_var)
        if val:
            cfg[base_key] = val
            logger.debug(
                "models_loader: '%s' overridden by env var %s", base_key, env_var
            )


def _resolve_fragment_env_vars(fragments: dict) -> None:
    """
    Resolve env var directives across all providers in a fragments dict.

    Recurses one level into nested dicts (e.g. serving_engine_defaults).
    Called BEFORE _deep_merge_missing — this enforces that models.yaml
    beats env vars: env resolution happens on the fragment, then
    models.yaml wins on merge.
    """
    for provider_cfg in fragments.values():
        if not isinstance(provider_cfg, dict):
            continue
        _resolve_env_keys(provider_cfg)
        # one level of recursion covers serving_engine_defaults
        for val in provider_cfg.values():
            if isinstance(val, dict):
                _resolve_env_keys(val)


def _deep_merge_missing(base: dict, additions: dict) -> None:
    """
    Fill keys missing from base with values from additions.

    base always wins on collision.
    Dicts: recursively merged (base wins at each nested level).
    Lists and scalars: atomic — base value is kept entirely.

    This enforces the precedence rule: models.yaml > fragment env var > fragment default.
    """
    for key, val in additions.items():
        if key not in base:
            # Key absent in base — fragment supplies it
            base[key] = val
        elif isinstance(base[key], dict) and isinstance(val, dict):
            # Both are dicts — recurse; base wins at every nested key
            _deep_merge_missing(base[key], val)
        # else: base wins — list or scalar, no merge, no override


def _load_fragments() -> Tuple[dict, dict]:
    """
    Discover all plugin model fragments via alems.models.fragments entry points.

    Duplicate provider name: first discovered wins, warning logged.
    Uses list(fragment) copy before pop — safe against dict mutation during iteration.
    Never raises — a broken fragment is logged and skipped so other fragments load.

    Returns:
        (merged fragments dict, provenance dict)
        provenance maps provider_name → {source, entry_point}
    """
    merged: dict = {}
    provenance: dict = {}

    for ep in entry_points(group="alems.models.fragments"):
        try:
            fragment = ep.load()
            # source = dotted module path → hyphenated package name for logging
            source = ep.value.split(":")[0].replace(".", "-")

            # Iterate over a copy — we may pop duplicate keys from fragment below
            for provider_name in list(fragment):
                if provider_name in merged:
                    logger.warning(
                        "models_loader: provider '%s' already registered by '%s'. "
                        "Ignoring duplicate from '%s'. "
                        "First-discovered plugin wins.",
                        provider_name,
                        provenance[provider_name]["source"],
                        source,
                    )
                    fragment.pop(provider_name)
                    continue
                provenance[provider_name] = {
                    "source": source,
                    "entry_point": ep.name,
                }

            _deep_merge_missing(merged, fragment)
            logger.info(
                "models_loader: loaded fragment from entry point '%s'", ep.name
            )
        except Exception as exc:
            logger.warning(
                "models_loader: failed to load fragment '%s' — %s", ep.name, exc
            )

    return merged, provenance


def _log_provenance(registry: dict, provenance: dict) -> None:
    """
    Log which fragment supplied each provider that ended up in the registry.

    Only logs providers that are present in the final registry — a fragment
    provider that was shadowed by a same-named models.yaml block still appears
    because _deep_merge_missing merged missing keys, not the full block.
    """
    for provider, meta in provenance.items():
        if provider in registry:
            logger.info(
                "models_loader: provider '%s' source=%s entry_point=%s",
                provider, meta["source"], meta["entry_point"],
            )
