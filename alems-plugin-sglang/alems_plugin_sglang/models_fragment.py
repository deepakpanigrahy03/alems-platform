"""
================================================================================
A-LEMS PLUGIN: alems-plugin-sglang — Model Fragment (8.6-B5)
================================================================================

Purpose:
    Declares the sglang_remote provider default configuration.
    Loaded by models_loader._load_fragments() via the alems.models.fragments
    entry point group.

    NOTE: sglang_remote was manually added to models.yaml in B4 as a temporary
    measure. After B5 ships and this fragment is active, the manually added
    sglang_remote block in models.yaml must be removed. The fragment becomes
    the authoritative source for the provider skeleton; only researcher
    overrides (base_url, custom model list) belong in models.yaml.

Env vars (resolved BEFORE merge into models.yaml):
    ALEMS_SGLANG_API_URL    — OpenAI-compatible base URL, includes /v1 suffix.
                              e.g. http://100.84.85.2:30000/v1
                              Set in ~/.alemsrc on each machine.

    ALEMS_SGLANG_ENGINE_URL — Serving engine root URL, no /v1 suffix.
                              Used by SGLangAdapter probes.
                              e.g. http://100.84.85.2:30000

Terminology:
    base_url  — OpenAI-compatible API base URL (/v1 suffix included).
    endpoint  — Serving engine root URL (no /v1).
    These are different fields and must never be aliased or merged.

8.6-B5 author: A-LEMS chunk 8.6-B5
================================================================================
"""

fragment = {
    "sglang_remote": {
        # Provider identity and access characteristics
        "is_local": False,
        "access_method": "api_http",
        "network_type": "lan",
        "captures_network_io": True,
        "energy_side": "client_only",
        "openai_compat": True,

        # base_url: OpenAI-compatible API base (includes /v1).
        # Localhost fallback — overridden by ALEMS_SGLANG_API_URL when set.
        # models.yaml base_url beats this even if the env var is set.
        "base_url": "http://localhost:30000/v1",
        "base_url_env": "ALEMS_SGLANG_API_URL",

        # api_key_env: runtime metadata — NOT a resolution directive.
        "api_key_env": None,

        "cost_class": "free",
        "priority": 2,
        "rate_limit_tpm": None,
        "execution_site": "lan_server",
        "transport": "lan_http",
        "remote_energy_available": True,

        # serving_engine_defaults: available to B4 experiment config resolver.
        # B5 does NOT apply these.
        "serving_engine_defaults": {
            "type": "sglang",
            "endpoint": "http://localhost:30000",
            "endpoint_env": "ALEMS_SGLANG_ENGINE_URL",
        },

        # Empty — runtime discovers actual models from GET /v1/models.
        "models": [],
    }
}
