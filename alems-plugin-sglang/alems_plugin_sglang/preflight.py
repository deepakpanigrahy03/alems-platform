"""
================================================================================
A-LEMS PLUGIN: alems-plugin-sglang — Preflight Check (8.6-B5)
================================================================================

Registered via alems.preflight.checks entry point group.
Called by core/utils/preflight._run_plugin_preflight() when provider=sglang_remote.

config dict is already fully resolved by models_loader.get_model() —
base_url has ALEMS_SGLANG_API_URL expanded from ~/.alemsrc.
No hardcoding of URLs here.
================================================================================
"""

import sys
import requests


def check(config: dict) -> None:
    """
    Verify SGLang server is reachable and serving at least one model.

    Args:
        config: fully resolved provider config from models_loader.get_model().
                base_url is already env-expanded — e.g. http://100.84.85.2:30000/v1
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        sys.exit("❌ sglang_remote: base_url not set — check ALEMS_SGLANG_API_URL in ~/.alemsrc")

    try:
        r = requests.get(f"{base_url}/models", timeout=3)
        if r.status_code != 200:
            sys.exit(
                f"❌ sglang_remote: server at {base_url} returned {r.status_code} — is it running?\n"
                f"   Start with: bash /opt/ai-stack/scripts/serve_llm.sh sglang <model>"
            )
        models = r.json().get("data", [])
        if not models:
            sys.exit(
                f"❌ sglang_remote: server at {base_url} has no models loaded\n"
                f"   Start with: bash /opt/ai-stack/scripts/serve_llm.sh sglang <model>"
            )
        print(f"✅ sglang_remote: OK — {models[0]['id']} at {base_url}")

    except requests.exceptions.ConnectionError:
        sys.exit(
            f"❌ sglang_remote: UNREACHABLE at {base_url}\n"
            f"   Start with: bash /opt/ai-stack/scripts/serve_llm.sh sglang <model>"
        )
    except Exception as e:
        sys.exit(f"❌ sglang_remote: health check failed — {e}")
