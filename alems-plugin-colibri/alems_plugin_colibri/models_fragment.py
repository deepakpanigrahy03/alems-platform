"""
================================================================================
A-LEMS PLUGIN: alems-plugin-colibri — Model Fragment (8.6-B5)
================================================================================

Purpose:
    Declares the colibri_remote provider default configuration.
    Loaded by models_loader._load_fragments() via the alems.models.fragments
    entry point group.

    Status: Colibri adapter is DEFERRED — no supported model available on
    GN100 (Colibri v1.12.0 supports GLM-5.2, Kimi K3, DeepSeek V4.1 Flash;
    Mistral-7B is unsupported). This fragment is code-complete and correct.
    It will activate automatically once a supported model is downloaded.

    Colibri telemetry is available via GET /health (JSON, not Prometheus).
    queue_metrics=True is set via ColibriAdapter._caps_from_probes() override.
    This fragment does not encode that detail — it is adapter-level logic.

Env vars (resolved BEFORE merge into models.yaml):
    ALEMS_COLIBRI_API_URL    — OpenAI-compatible base URL, includes /v1 suffix.
                                e.g. http://100.84.85.2:8001/v1
                                Set in ~/.alemsrc on each machine.

    ALEMS_COLIBRI_ENGINE_URL — Serving engine root URL, no /v1 suffix.
                                Used by ColibriAdapter probes (GET /health).
                                e.g. http://100.84.85.2:8001

Terminology:
    base_url  — OpenAI-compatible API base URL (/v1 suffix included).
    endpoint  — Serving engine root URL (no /v1).
    These are different fields and must never be aliased or merged.

8.6-B5 author: A-LEMS chunk 8.6-B5
================================================================================
"""

fragment = {
    "colibri_remote": {
        # Provider identity and access characteristics
        "is_local": False,
        "access_method": "api_http",
        "network_type": "lan",
        "captures_network_io": True,
        "energy_side": "client_only",
        "openai_compat": True,

        # base_url: OpenAI-compatible API base (includes /v1).
        # Localhost fallback — overridden by ALEMS_COLIBRI_API_URL when set.
        # models.yaml base_url beats this even if the env var is set.
        "base_url": "http://localhost:8001/v1",
        "base_url_env": "ALEMS_COLIBRI_API_URL",

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
        # Colibri telemetry comes from /health, not /metrics.
        # That routing lives in ColibriAdapter._caps_from_probes(), not here.
        "serving_engine_defaults": {
            "type": "colibri",
            "endpoint": "http://localhost:8001",
            "endpoint_env": "ALEMS_COLIBRI_ENGINE_URL",
        },

        # Empty — runtime discovers actual models from GET /v1/models.
        "models": [],
    }
}
