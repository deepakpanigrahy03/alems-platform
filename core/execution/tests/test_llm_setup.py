#!/usr/bin/env python3
"""
A-LEMS LLM Setup Tester
--------------------------
Auto-discovers all providers and models from config/models.yaml.
Tests connectivity with a minimal inference call per provider.

Usage:
    python3 core/execution/tests/test_llm_setup.py
    python3 core/execution/tests/test_llm_setup.py --provider groq
    python3 core/execution/tests/test_llm_setup.py --list
    python3 core/execution/tests/test_llm_setup.py --provider all --verbose
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

from core.config_loader import ConfigLoader


TEST_PROMPT = "What is 2+2? Reply with one word only."
TEST_MAX_TOKENS = 16
TIMEOUT_SECONDS = 15


def _load_provider_key_vars() -> dict:
    """Read api_key_env per provider directly from models.yaml."""
    import yaml
    project_root = Path(__file__).resolve().parents[3]
    models_yaml = project_root / "config" / "models.yaml"
    try:
        raw = yaml.safe_load(models_yaml.read_text())
        providers = raw.get("providers", {})
        return {
            name: pdata.get("api_key_env", "")
            for name, pdata in providers.items()
        }
    except Exception:
        return {}


# Load once at module level
_PROVIDER_KEY_VARS = _load_provider_key_vars()


def check_api_key(provider_name: str, models: list) -> tuple:
    """
    Check if the API key for this provider is set.
    Returns (key_set: bool, key_var: str, key_preview: str)
    """
    key_var = _PROVIDER_KEY_VARS.get(provider_name, "")
    if not key_var:
        return True, "", ""  # local providers need no key
    key_val = os.environ.get(key_var, "")
    if key_val:
        preview = key_val[:8] + "..." if len(key_val) > 8 else key_val
        return True, key_var, preview
    return False, key_var, ""


def test_provider_connectivity(
    config: ConfigLoader,
    provider_name: str,
    verbose: bool = False
) -> dict:
    """
    Test one provider with a minimal inference call.
    Returns a result dict with status, latency, model_id, and error.
    """
    result = {
        "provider": provider_name,
        "status": "unknown",
        "model_id": None,
        "latency_ms": None,
        "error": None,
        "key_set": False,
        "key_var": None,
    }

    models = config.list_models(provider_name)
    if not models:
        result["status"] = "no_models"
        result["error"] = "No models configured for this provider"
        return result

    # Find first available model
    available = [m for m in models if m.get("available", True)]
    if not available:
        result["status"] = "no_models"
        result["error"] = "All models marked available=false"
        return result

    model = available[0]
    result["model_id"] = model.get("model_id", "unknown")

    # Check API key
    key_set, key_var, key_preview = check_api_key(provider_name, models)
    result["key_set"] = key_set
    result["key_var"] = key_var

    if not key_set and key_var:
        result["status"] = "no_key"
        result["error"] = f"{key_var} not set in ~/.alemsrc"
        return result

    if verbose:
        print(f"    Provider: {provider_name}")
        print(f"    Model:    {result['model_id']}")
        if key_var:
            print(f"    Key:      {key_var} = {key_preview}")

    # Attempt inference
    try:
        model_config = config.get_model_config_v2(
            provider_name,
            result["model_id"],
        )
        if not model_config:
            result["status"] = "config_error"
            result["error"] = "get_model_config_v2 returned None"
            return result

        start = time.time()
        _run_inference(provider_name, model_config, verbose)
        elapsed_ms = (time.time() - start) * 1000

        result["status"] = "ok"
        result["latency_ms"] = round(elapsed_ms, 0)

    except TimeoutError:
        result["status"] = "timeout"
        result["error"] = f"No response within {TIMEOUT_SECONDS}s"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)[:120]

    return result


def _run_inference(provider_name: str, model_config: dict, verbose: bool):
    """
    Run one minimal inference call using the appropriate client.
    Raises on failure.
    """
    import signal

    def _timeout_handler(signum, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)

    try:
        transport = model_config.get("transport", "remote_http")
        provider = model_config.get("provider", provider_name)
        model_id = model_config.get("model_id", "")
        base_url = model_config.get("base_url", "")
        api_key_env = model_config.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""

        if transport == "inprocess":
            # llama_cpp — skip connectivity test, just verify config exists
            if verbose:
                print(f"    Transport: inprocess (config verified, no call made)")
            return

        if transport in ("loopback_http", "remote_http", "lan_http"):
            import urllib.request
            import json

            if not base_url:
                raise ValueError(f"No base_url configured for {provider_name}")

            payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": TEST_PROMPT}],
                "max_tokens": TEST_MAX_TOKENS,
                "temperature": 0,
            }

            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}" if api_key else "Bearer none",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read())
                if verbose:
                    content = data["choices"][0]["message"]["content"]
                    print(f"    Response: {content.strip()[:60]}")
            return

        # TTS / STT providers — skip inference, verify config only
        tasks = model_config.get("tasks", [])
        if any(t in tasks for t in ["text-to-speech", "speech-to-text", "voice-cloning"]):
            if verbose:
                print(f"    Tasks: {tasks} (config verified, no call made)")
            return

        raise ValueError(f"Unknown transport: {transport}")

    finally:
        signal.alarm(0)


def print_result(result: dict, verbose: bool = False):
    """Print one provider result row."""
    provider = result["provider"]
    status = result["status"]
    model_id = result.get("model_id") or ""
    latency = result.get("latency_ms")
    error = result.get("error") or ""

    STATUS_SYMBOLS = {
        "ok":           "  OK  ",
        "no_key":       "NO KEY",
        "no_models":    "  --  ",
        "timeout":      " TOUT ",
        "config_error": " CONF ",
        "error":        " FAIL ",
        "unknown":      "  ?   ",
    }

    symbol = STATUS_SYMBOLS.get(status, "  ?   ")
    latency_str = f"{latency:.0f}ms" if latency else "    "

    model_short = model_id[:35] if model_id else ""
    print(f"  [{symbol}] {provider:<20} {model_short:<36} {latency_str:<8}", end="")

    if error and (verbose or status not in ("ok", "no_models")):
        print(f"  {error}")
    else:
        print()


def list_all(config: ConfigLoader):
    """Print all providers and models without testing."""
    providers = config.list_providers()
    print(f"\n  {len(providers)} providers configured in models.yaml\n")

    for provider_name in providers:
        models = config.list_models(provider_name)
        available = [m for m in models if m.get("available", True)]
        key_set, key_var, _ = check_api_key(provider_name, models)
        if key_var:
            key_indicator = "✓" if key_set else "✗"
            key_display = f"{key_var} {'set' if key_set else 'MISSING'}"
        else:
            key_indicator = "✓"
            key_display = "no key needed"
        print(f"  {provider_name:<22} {len(available):>2} models  key:{key_indicator}  ({key_display})")
        for m in available:
            tasks = m.get("tasks", ["text-generation"])
            print(f"      {m.get('model_id',''):<40} {tasks}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="""
A-LEMS LLM Setup Tester
-----------------------
Auto-discovers all providers from config/models.yaml and tests
connectivity with a minimal inference call per provider.

Examples:
  python3 test_llm_setup.py                      test all providers
  python3 test_llm_setup.py --list               list providers and models
  python3 test_llm_setup.py --provider groq      test groq only
  python3 test_llm_setup.py --verbose            show response content
  python3 test_llm_setup.py --provider all       test all (explicit)

Status codes:
  OK     — inference call succeeded
  NO KEY — API key variable not set in ~/.alemsrc
  FAIL   — call returned an error
  TOUT   — no response within 15 seconds
  --     — no available models configured
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        default="all",
        metavar="NAME",
        help="Provider name from models.yaml, or 'all' to test every provider (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all configured providers and models without making any API calls",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show model config details and response content per provider",
    )
    args = parser.parse_args()

    # Load ~/.alemsrc
    alemsrc = Path.home() / ".alemsrc"
    if alemsrc.exists():
        for line in alemsrc.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    config = ConfigLoader()
    providers = config.list_providers()

    if args.list:
        list_all(config)
        return

    # Select providers to test
    if args.provider == "all":
        to_test = providers
    else:
        if args.provider not in providers:
            print(f"  Provider '{args.provider}' not found.")
            print(f"  Available: {', '.join(providers)}")
            sys.exit(1)
        to_test = [args.provider]

    print(f"\n  Testing {len(to_test)} provider(s)...\n")
    print(f"  {'STATUS':<8} {'PROVIDER':<20} {'MODEL':<36} {'LATENCY'}")
    print(f"  {'-'*7} {'-'*20} {'-'*36} {'-'*8}")

    results = []
    for provider_name in to_test:
        if args.verbose:
            print(f"\n  --- {provider_name} ---")
        result = test_provider_connectivity(config, provider_name, args.verbose)
        results.append(result)
        if not args.verbose:
            print_result(result, verbose=False)
        else:
            print_result(result, verbose=True)

    # Summary
    ok = sum(1 for r in results if r["status"] == "ok")
    no_key = sum(1 for r in results if r["status"] == "no_key")
    failed = sum(1 for r in results if r["status"] in ("error", "timeout", "config_error"))
    skipped = sum(1 for r in results if r["status"] == "no_models")

    print(f"\n  {ok} passed  {no_key} no key  {failed} failed  {skipped} skipped\n")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
