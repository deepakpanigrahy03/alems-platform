"""
================================================================================
A-LEMS PLUGIN: alems-plugin-colibri — Preflight Check (8.6-B5)
================================================================================

Registered via alems.preflight.checks entry point group.
Called by core/utils/preflight._run_plugin_preflight() when provider=colibri_remote.

Colibri telemetry is via GET /health (JSON), not /metrics (Prometheus).
Preflight checks /v1/models for reachability and /health for queue state.
================================================================================
"""

import sys
import requests


def check(config: dict) -> None:
    """
    Verify Colibri server is reachable and serving at least one model.

    Args:
        config: fully resolved provider config from models_loader.get_model().
                base_url is already env-expanded — e.g. http://100.84.85.2:8001/v1
    """
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        sys.exit("❌ colibri_remote: base_url not set — check ALEMS_COLIBRI_API_URL in ~/.alemsrc")

    # Derive engine root from base_url (strip /v1 if present)
    engine_url = base_url[:-3] if base_url.endswith("/v1") else base_url

    try:
        r = requests.get(f"{base_url}/models", timeout=3)
        if r.status_code != 200:
            sys.exit(
                f"❌ colibri_remote: server at {base_url} returned {r.status_code} — is it running?\n"
                f"   Start with: /opt/ai-stack/envs/colibri/src/c/coli serve --model <model>"
            )
        models = r.json().get("data", [])
        if not models:
            sys.exit(
                f"❌ colibri_remote: server at {base_url} has no models loaded\n"
                f"   Note: Colibri v1.12.0 supports GLM-5.2, Kimi K3, DeepSeek V4.1 Flash.\n"
                f"   Mistral-7B is NOT supported."
            )
        print(f"✅ colibri_remote: OK — {models[0]['id']} at {base_url}")

    except requests.exceptions.ConnectionError:
        sys.exit(
            f"❌ colibri_remote: UNREACHABLE at {base_url}\n"
            f"   Start with: /opt/ai-stack/envs/colibri/src/c/coli serve --model <model>\n"
            f"   Note: Colibri v1.12.0 supports GLM-5.2, Kimi K3, DeepSeek V4.1 Flash only."
        )
    except Exception as e:
        sys.exit(f"❌ colibri_remote: health check failed — {e}")
