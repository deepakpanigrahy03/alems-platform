#!/usr/bin/env python3
"""Pre-flight validation for A-LEMS experiments."""

import os
import sys
from pathlib import Path
import logging
progress = logging.getLogger("alems.progress")
logger = logging.getLogger(__name__)

def get_env(key):
    """Get env var from environment or .env file."""
    val = os.getenv(key)
    if val:
        return val
    env_file = Path("core/.env")
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.strip().split("=", 1)[1]
    return None


def check_powermetrics():
    """Check powermetrics can run non-interactively (Mac only)."""
    import subprocess
    result = subprocess.run(
        ["sudo", "-n", "powermetrics", "--samplers", "cpu_power", "-n", "1", "-i", "100"],
        capture_output=True,
        timeout=5,
    )
    if result.returncode != 0:
        sys.exit(
            "   powermetrics requires sudo access for energy measurement.\n"
            "   Ask an admin to run: sudo bash scripts/fix_permissions.sh\n"
            "   Then verify with: sudo -n powermetrics --samplers cpu_power -n 1 -i 100"
        )
    progress.info("preflight  powermetrics ok (non interactive sudo)")


def check_msr():
    """Check MSR helper."""
    msr = Path("core/msr_helper/msr_read")
    if not msr.exists():
        sys.exit("MSR helper not found. Run: sudo ./scripts/fix_permissions.sh")
    if not os.access(msr, os.X_OK):
        sys.exit("MSR helper not executable. Run: sudo chmod +x core/msr_helper/msr_read")
    progress.info("preflight  msr helper ok")


def check_configs():
    """Check essential config files exist."""
    required = [
        "config/models.json",
        "config/hw_config.json",
        "config/app_settings.yaml",
    ]
    for f in required:
        if not Path(f).exists():
            sys.exit(f"Config file missing: {f}")
    progress.info("preflight  config files ok")


def check_cloud(config):
    """Check cloud API."""
    key = get_env(config.get("api_key_env", "GROQ_API_KEY"))
    if not key:
        sys.exit("API key not found in environment or core/.env")
    
    import requests
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=3
        )
        if r.status_code != 200:
            sys.exit(f"API key invalid: {r.status_code}")
    except Exception as e:
        sys.exit(f"API test failed: {e}")
    progress.info("preflight  cloud api ok")


def check_local(config):
    """Check local LLM."""
    try:
        import llama_cpp
    except ImportError:
        sys.exit("llama-cpp-python not installed. Run: pip install llama-cpp-python")
    progress.info("preflight  llama-cpp-python ok")
    
    from scripts.tools.path_loader import get_models_dir
    raw = config.get("model_path", "")
    model = raw.replace("{models_dir}", get_models_dir())
    if model and not Path(model).exists():
        sys.exit(f"Model not found: {model}")
    progress.info("preflight  model file %s", model)


def check_vllm(base_url: str):
    """Check vLLM server is reachable and serving models."""
    import requests
    try:
        r = requests.get(f"{base_url}/models", timeout=3)
        if r.status_code != 200:
            sys.exit(f"vLLM server at {base_url} returned {r.status_code} — is it running?")
        models = r.json().get("data", [])
        if not models:
            sys.exit(f"vLLM server at {base_url} has no models loaded")
        progress.info("preflight  vllm %s at %s", models[0]["id"], base_url)
    except requests.exceptions.ConnectionError:
        sys.exit(f"vLLM provider unreachable at {base_url}; start it with: bash /opt/ai-stack/scripts/serve_llm.sh <model>")
    except Exception as e:
        sys.exit(f"vLLM health check failed: {e}")


def check_groq(config):
    """Check Groq API key is set and reachable."""
    key = get_env(config.get("api_key_env", "GROQ_API_KEY"))
    if not key:
        sys.exit("GROQ_API_KEY not found in environment or core/.env")
    progress.info("preflight  groq api key ok")


def _run_plugin_preflight(provider, config):
    # type: (str, dict) -> bool
    """
    Discover and run plugin-owned preflight check via alems.preflight.checks
    entry point group. Returns True if a plugin check ran, False if none
    registered. Never raises — broken plugin is logged and skipped.
    """
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="alems.preflight.checks"):
            if ep.name == provider:
                check_fn = ep.load()
                check_fn(config)
                return True
    except Exception as e:
        logger.warning("plugin preflight for %s failed to load: %s", provider, e)
    return False


def preflight(executor, provider):
    """Run checks."""
    progress.info("preflight  checks")
    check_configs()

    # Platform-specific energy measurement gate
    import json
    hw_config_path = Path("config/hw_config.json")
    if hw_config_path.exists():
        with open(hw_config_path) as f:
            hw = json.load(f)
        platform_class = hw.get("platform_class", "")
        if platform_class in ("apple_silicon", "intel_mac"):
            check_powermetrics()
        else:
            check_msr()
    else:
        check_msr()
    if provider == "vllm_local":
        # vllm_local uses built-in check for backward compat.
        base_url = executor.config.get("base_url", "http://localhost:8000/v1")
        check_vllm(base_url)
    elif provider == "vllm_remote":
        # vllm_remote routes through plugin preflight (B6-1).
        # Falls back to built-in check if no plugin is registered.
        if not _run_plugin_preflight(provider, executor.config):
            base_url = executor.config.get("base_url", "http://localhost:8000/v1")
            check_vllm(base_url)
    elif provider == "groq":
        check_groq(executor.config)
    elif provider == "cloud":
        check_cloud(executor.config)
    elif provider == "llama_cpp":
        check_local(executor.config)
    else:
        # Plugin-owned preflight — any installed plugin registers
        # alems.preflight.checks.<provider_name> entry point.
        # base_url is already resolved from ~/.alemsrc by models_loader.
        if not _run_plugin_preflight(provider, executor.config):
            logger.info("no health check registered for provider %s; skipped", provider)
    progress.info("preflight  all checks passed")


if __name__ == "__main__":
    import argparse
    from core.config_loader import ConfigLoader
    
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["cloud", "local"], required=True)
    args = p.parse_args()
    
    cfg = ConfigLoader().get_model_config(args.provider, "linear")
    preflight(type('obj', (object,), {'config': cfg})(), args.provider)