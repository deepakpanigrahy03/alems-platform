# tests/test_sdk_boundary_serving.py
# 39.5.1a WP 1a.1: the serving and generation contracts live in alems_sdk,
# core paths re-export the identical objects, and the SDK plus every plugin
# imports with core made unimportable (INV-14, D2.1).
# Run: venv/bin/python -m pytest tests/test_sdk_boundary_serving.py -v
import subprocess
import sys

import pytest

# Snippet run in a fresh interpreter: core is blocked, so any import of core
# (direct or transitive) raises ImportError and fails the test.
_BLOCK_CORE = (
    "import sys; sys.modules['core'] = None; sys.modules['scripts'] = None\n"
)

_SDK_MODULES = [
    "alems_sdk.serving",
    "alems_sdk.serving.config",
    "alems_sdk.serving.discovery",
    "alems_sdk.generation",
]

_PLUGIN_MODULES = [
    "alems_plugin_colibri.adapter",
    "alems_plugin_llama_cpp.adapter",
    "alems_plugin_ollama.adapter",
    "alems_plugin_sglang.adapter",
    "alems_plugin_tensorrt_llm.adapter",
    "alems_plugin_vllm.adapter",
]


def _import_without_core(module: str) -> subprocess.CompletedProcess:
    """Import module in a fresh interpreter with core and scripts blocked."""
    code = _BLOCK_CORE + "import %s\n" % module
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )


@pytest.mark.parametrize("module", _SDK_MODULES + _PLUGIN_MODULES)
def test_imports_without_core(module):
    """SDK modules and plugin adapters must not need core (INV-14)."""
    result = _import_without_core(module)
    assert result.returncode == 0, result.stderr[-2000:]


def test_core_paths_are_sdk_objects():
    """Core compatibility paths re-export the identical SDK objects."""
    import alems_sdk.generation as gen
    import alems_sdk.serving as srv
    import alems_sdk.serving.discovery as disc
    from core.execution.adapters import base as core_base
    from core.serving import discovery as core_disc
    from core.serving import serving_adapter as core_srv

    assert core_base.TextGenABC is gen.TextGenABC
    assert core_base.MediaABC is gen.MediaABC
    assert core_disc.CapabilityDiscoveryMixin is disc.CapabilityDiscoveryMixin
    for name in ("ServingEngineAdapter", "ServingCapabilities", "RequestMetrics",
                 "CacheState", "ExpertTierState", "QueueState",
                 "TokenRateState", "EngineInfo"):
        assert getattr(core_srv, name) is getattr(srv, name), name


def test_core_adapters_keep_helpers():
    """Core adapters that used the runtime helpers still have them (Rule S)."""
    from core.execution.adapters.base import BaseAdapterMixin
    from core.execution.adapters.llama_cpp import LlamaCppAdapter
    from core.execution.adapters.openai_compat import OpenAICompatAdapter

    for cls in (LlamaCppAdapter, OpenAICompatAdapter):
        assert issubclass(cls, BaseAdapterMixin), cls.__name__


def test_resolve_endpoint_precedence():
    """Experiment block over fleet defaults over environment."""
    from alems_sdk.serving.config import resolve_endpoint

    env = {"ALEMS_VLLM_ENGINE_URL": "http://env:1"}
    fleet = {"endpoint": "http://fleet:2", "metrics_path": "/m2", "probe_timeout_s": 5}

    ep = resolve_endpoint({}, "vllm", fleet_defaults={}, env=env)
    assert ep.endpoint == "http://env:1" and ep.metrics_path == "/metrics"

    ep = resolve_endpoint({}, "vllm", fleet_defaults=fleet, env=env)
    assert ep.endpoint == "http://fleet:2" and ep.metrics_path == "/m2"
    assert ep.probe_timeout_s == 5.0

    exp = {"endpoint": "http://exp:3", "type": "vllm", "custom": 1}
    ep = resolve_endpoint(exp, "vllm", fleet_defaults=fleet, env=env)
    assert ep.endpoint == "http://exp:3"
    assert ep.extra == {"custom": 1}          # name and type never leak to extra

    ep = resolve_endpoint({}, "vllm", fleet_defaults={}, env={})
    assert ep.endpoint is None                # adapter must degrade


def test_core_resolve_dict_shape_unchanged():
    """AdapterConfig.resolve keeps its historical keys."""
    from core.serving.adapter_config import AdapterConfig

    d = AdapterConfig.resolve({"endpoint": "http://x:1"}, "vllm")
    assert set(d) == {"endpoint", "metrics_path", "health_path", "models_path",
                      "probe_timeout_s", "request_level_correlation", "extra"}
    assert d["endpoint"] == "http://x:1"
