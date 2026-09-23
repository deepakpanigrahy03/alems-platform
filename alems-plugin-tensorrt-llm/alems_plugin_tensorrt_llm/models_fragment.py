"""
================================================================================
A-LEMS PLUGIN: alems-plugin-tensorrt-llm — Model Fragment (8.6-B5)
================================================================================

Purpose:
    Declares the tensorrt_llm_remote provider default configuration.
    Loaded by models_loader._load_fragments() via the alems.models.fragments
    entry point group.

    Status: TRT-LLM adapter is DEFERRED (ABI mismatch on GN100 — public
    pytorch.org wheel incompatible with TRT-LLM 1.2.1, requires NGC container
    torch build). This fragment is code-complete and correct.
    It will activate automatically once the TRT-LLM ABI issue is resolved
    and the plugin is pip-installed.

    TRT-LLM uses a non-standard Prometheus path: /prometheus/metrics
    (not /metrics). This is reflected in adapters.yaml and the adapter
    _caps_from_probes() override. This fragment does not need to encode that
    detail — it is adapter-level configuration.

Env vars (resolved BEFORE merge into models.yaml):
    ALEMS_TRT_LLM_API_URL    — OpenAI-compatible base URL, includes /v1 suffix.
                                e.g. http://100.84.85.2:8001/v1
                                Set in ~/.alemsrc on each machine.

    ALEMS_TRT_LLM_ENGINE_URL — Serving engine root URL, no /v1 suffix.
                                Used by TensorRTLLMAdapter probes.
                                e.g. http://100.84.85.2:8001

Terminology:
    base_url  — OpenAI-compatible API base URL (/v1 suffix included).
    endpoint  — Serving engine root URL (no /v1).
    These are different fields and must never be aliased or merged.

8.6-B5 author: A-LEMS chunk 8.6-B5
================================================================================
"""

fragment = {
    "tensorrt_llm_remote": {
        # Provider identity and access characteristics
        "is_local": False,
        "access_method": "api_http",
        "network_type": "lan",
        "captures_network_io": True,
        "energy_side": "client_only",
        "openai_compat": True,

        # base_url: OpenAI-compatible API base (includes /v1).
        # Localhost fallback — overridden by ALEMS_TRT_LLM_API_URL when set.
        # models.yaml base_url beats this even if the env var is set.
        "base_url": "http://localhost:8001/v1",
        "base_url_env": "ALEMS_TRT_LLM_API_URL",

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
        # Note: metrics_path for TRT-LLM is /prometheus/metrics (non-standard).
        # That path lives in adapters.yaml, not here.
        "serving_engine_defaults": {
            "type": "tensorrt_llm",
            "endpoint": "http://localhost:8001",
            "endpoint_env": "ALEMS_TRT_LLM_ENGINE_URL",
        },

        # Empty — runtime discovers actual models from GET /v1/models.
        "models": [],
    }
}
