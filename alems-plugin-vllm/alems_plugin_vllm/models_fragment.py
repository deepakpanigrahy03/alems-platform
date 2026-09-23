"""
================================================================================
A-LEMS PLUGIN: alems-plugin-vllm — Model Fragment (8.6-B5)
================================================================================

Purpose:
    Declares the vllm_remote provider default configuration.
    Loaded by models_loader._load_fragments() via the alems.models.fragments
    entry point group.

    This fragment supplies the provider block that models.yaml would otherwise
    require a researcher to write by hand.
    models.yaml always wins on any key collision — this is the fallback layer.

Env vars (resolved BEFORE merge into models.yaml):
    ALEMS_VLLM_API_URL     — OpenAI-compatible base URL, includes /v1 suffix.
                             e.g. http://100.84.85.2:8000/v1
                             Set in ~/.alemsrc on each machine.

    ALEMS_VLLM_ENGINE_URL  — Serving engine root URL, no /v1 suffix.
                             Used by VLLMAdapter probes in serving/registry.
                             e.g. http://100.84.85.2:8000
                             Set via adapters.yaml or ~/.alemsrc.

Terminology:
    base_url  — OpenAI-compatible API base URL (/v1 suffix included).
    endpoint  — Serving engine root URL (no /v1).
    These are different fields and must never be aliased or merged.

8.6-B5 author: A-LEMS chunk 8.6-B5
================================================================================
"""

# fragment is the dict that models_loader._load_fragments() reads.
# It is a flat provider-keyed dict — same shape as models.yaml providers block.
# models_loader merges this into raw_providers BEFORE yaml expansion runs.
fragment = {
    "vllm_remote": {
        # Provider identity and access characteristics
        "is_local": False,
        "access_method": "api_http",
        "network_type": "lan",
        "captures_network_io": True,
        "energy_side": "client_only",
        "openai_compat": True,

        # base_url: OpenAI-compatible API base (includes /v1).
        # Localhost fallback — overridden by ALEMS_VLLM_API_URL when set.
        # models.yaml base_url beats this even if the env var is set.
        "base_url": "http://localhost:8000/v1",
        "base_url_env": "ALEMS_VLLM_API_URL",

        # api_key_env: runtime metadata — NOT a resolution directive.
        # Excluded from _resolve_env_keys by _ENV_METADATA_KEYS.
        # Survives into registry for use by inference client at runtime.
        "api_key_env": None,

        "cost_class": "free",
        "priority": 2,
        "rate_limit_tpm": None,
        "execution_site": "lan_server",
        "transport": "lan_http",
        "remote_energy_available": True,

        # serving_engine_defaults: available to B4 experiment config resolver.
        # B5 does NOT apply these — B4 reads them at experiment runtime.
        # endpoint_env resolves to ALEMS_VLLM_ENGINE_URL at adapter startup.
        "serving_engine_defaults": {
            "type": "vllm",
            "endpoint": "http://localhost:8000",
            "endpoint_env": "ALEMS_VLLM_ENGINE_URL",
        },

        # models list is empty — plugin knows the engine type, not the deployed model.
        # The serving adapter discovers actual models from GET /v1/models at startup.
        "models": [],
    }
}
