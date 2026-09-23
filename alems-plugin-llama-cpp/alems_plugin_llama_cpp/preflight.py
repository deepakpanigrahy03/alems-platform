"""
================================================================================
A-LEMS PLUGIN: alems-plugin-llama-cpp — Preflight Check (8.6-B5)
================================================================================

Registered via alems.preflight.checks entry point group.
Called by core/utils/preflight._run_plugin_preflight() when provider=llamacpp_remote.

config dict is already fully resolved by models_loader.get_model() —
base_url has ALEMS_LLAMA_CPP_API_URL expanded from ~/.alemsrc.
No hardcoding of URLs here.

Two llama.cpp server variants:
    llama-cpp-python: /models returns 200 but /metrics returns 404.
    llama-server C++ binary: /models 200 + /metrics 200 with Prometheus output.
Preflight only checks /models — telemetry availability is adapter's concern.
================================================================================
"""

import sys
import requests


def check(config: dict) -> None:
    """
    Verify llama.cpp server is reachable and serving at least one model.

    Args:
        config: fully resolved provider config from models_loader.get_model().
                base_url is already env-expanded — e.g. http://100.84.85.2:8080/v1
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        sys.exit("❌ llamacpp_remote: base_url not set — check ALEMS_LLAMA_CPP_API_URL in ~/.alemsrc")

    try:
        r = requests.get(f"{base_url}/models", timeout=3)
        if r.status_code != 200:
            sys.exit(
                f"❌ llamacpp_remote: server at {base_url} returned {r.status_code} — is it running?\n"
                f"   Start llama-server: llama-server --metrics --port 8080 -m <model.gguf>\n"
                f"   Or llama-cpp-python: python -m llama_cpp.server --model <model.gguf> --port 8080"
            )
        models = r.json().get("data", [])
        if not models:
            sys.exit(
                f"❌ llamacpp_remote: server at {base_url} has no models loaded\n"
                f"   Restart with a model path specified."
            )
        print(f"✅ llamacpp_remote: OK — {models[0]['id']} at {base_url}")

    except requests.exceptions.ConnectionError:
        sys.exit(
            f"❌ llamacpp_remote: UNREACHABLE at {base_url}\n"
            f"   Start llama-server: llama-server --metrics --port 8080 -m <model.gguf>\n"
            f"   Or llama-cpp-python: python -m llama_cpp.server --model <model.gguf> --port 8080"
        )
    except Exception as e:
        sys.exit(f"❌ llamacpp_remote: health check failed — {e}")
