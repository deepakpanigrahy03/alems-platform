"""
================================================================================
A-LEMS PLUGIN: alems-plugin-tensorrt-llm — Preflight Check (8.6-B5)
================================================================================

Registered via alems.preflight.checks entry point group.
Called by core/utils/preflight._run_plugin_preflight() when provider=tensorrt_llm_remote.

Status: deferred — TRT-LLM ABI mismatch on GN100 (requires NGC container torch).
This check activates automatically once the ABI issue is resolved.
================================================================================
"""

import sys
import requests


def check(config: dict) -> None:
    """
    Verify TensorRT-LLM server is reachable and serving at least one model.

    Args:
        config: fully resolved provider config from models_loader.get_model().
                base_url is already env-expanded — e.g. http://100.84.85.2:8001/v1
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        sys.exit("❌ tensorrt_llm_remote: base_url not set — check ALEMS_TRT_LLM_API_URL in ~/.alemsrc")

    try:
        r = requests.get(f"{base_url}/models", timeout=3)
        if r.status_code != 200:
            sys.exit(
                f"❌ tensorrt_llm_remote: server at {base_url} returned {r.status_code} — is it running?\n"
                f"   Start with: trtllm-serve <model> --port 8001\n"
                f"   Note: requires NGC container torch build — public pytorch.org wheel has ABI mismatch."
            )
        models = r.json().get("data", [])
        if not models:
            sys.exit(
                f"❌ tensorrt_llm_remote: server at {base_url} has no models loaded."
            )
        print(f"✅ tensorrt_llm_remote: OK — {models[0]['id']} at {base_url}")

    except requests.exceptions.ConnectionError:
        sys.exit(
            f"❌ tensorrt_llm_remote: UNREACHABLE at {base_url}\n"
            f"   Start with: trtllm-serve <model> --port 8001\n"
            f"   Note: requires NGC container torch build — public pytorch.org wheel has ABI mismatch."
        )
    except Exception as e:
        sys.exit(f"❌ tensorrt_llm_remote: health check failed — {e}")
