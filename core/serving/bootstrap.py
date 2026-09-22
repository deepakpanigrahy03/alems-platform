"""
================================================================================
SERVING ENGINE BOOTSTRAP  —  core/serving/bootstrap.py
================================================================================

PURPOSE:
    Registers the built-in RemoteAPIAdapter and triggers entry-point
    discovery for installed engine plugins.

    Import this module once (in experiment_runner.py startup) and all
    built-in and installed adapters are available in the registry.

    Pattern mirrors core/execution/adapters/bootstrap.py.

    Only RemoteAPIAdapter is registered here from core.
    All other engines (vLLM, SGLang, llama.cpp, TensorRT-LLM, Colibri)
    are optional pip packages that self-register via the
    'alems.engines.serving' entry-point group.

USAGE (in experiment_runner.py):
    import core.serving.bootstrap  # noqa: F401 — side-effect import

AUTHOR: Deepak Panigrahy
================================================================================
"""

from core.serving.registry import ServingEngineRegistry
from core.serving.remote_api_adapter import RemoteAPIAdapter

# Register the only built-in adapter.
# vLLMAdapter and others are NOT registered here — they live in pip plugins.
ServingEngineRegistry.register(RemoteAPIAdapter)

# Discover all installed engine plugins via entry points.
# This is safe to call here because register() above already ran —
# discover() checks for duplicates and will error on collision (INV-6).
ServingEngineRegistry.discover()
