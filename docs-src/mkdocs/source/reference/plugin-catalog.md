# Plugin Catalog

A-LEMS is extended through plugins.
A plugin is a pip-installable package that registers itself via Python
entry points — no changes to core A-LEMS code required.
Install a plugin and A-LEMS discovers it automatically at startup.

## Installing a plugin

Always install plugins via the A-LEMS venv, not system pip:

```bash
venv/bin/pip install alems-plugin-vllm
```

System pip will fail because `alems-platform` is only visible inside
the venv.
After install, verify the plugin registered:

```bash
venv/bin/python3 -c "
from importlib.metadata import entry_points
for g in ['alems.engines.serving','alems.models.fragments','alems.preflight.checks']:
    print(g, '->', [ep.name for ep in entry_points(group=g)])
"
```

## Serving engine plugins

Serving engine plugins connect A-LEMS to a running LLM inference server
and retrieve KV cache telemetry, queue depth, and token throughput.
Each plugin registers three entry points: adapter, model fragment, and
preflight health check.

| Plugin | Provider | Hardware | Status |
|---|---|---|---|
| alems-plugin-vllm | vllm_remote | NVIDIA GPU, CUDA | stable |
| alems-plugin-sglang | sglang_remote | NVIDIA GPU, CUDA | stable |
| alems-plugin-llama-cpp | llamacpp_remote | CPU, any GPU | stable |
| alems-plugin-colibri | colibri_remote | NVIDIA GPU, MoE specialist | stable (deferred: no supported model on GN100) |
| alems-plugin-tensorrt-llm | tensorrt_llm_remote | NVIDIA GPU, NGC container required | stable (deferred: NGC container path) |

### Required ~/.alemsrc entries per plugin

Each serving engine plugin requires two env vars on every machine:

```bash
# vLLM
export ALEMS_VLLM_API_URL=http://<host>:8000/v1
export ALEMS_VLLM_ENGINE_URL=http://<host>:8000

# SGLang
export ALEMS_SGLANG_API_URL=http://<host>:30000/v1
export ALEMS_SGLANG_ENGINE_URL=http://<host>:30000

# llama.cpp
export ALEMS_LLAMA_CPP_API_URL=http://<host>:8080/v1
export ALEMS_LLAMA_CPP_ENGINE_URL=http://<host>:8080

# Colibri
export ALEMS_COLIBRI_API_URL=http://<host>:8001/v1
export ALEMS_COLIBRI_ENGINE_URL=http://<host>:8001

# TRT-LLM
export ALEMS_TRT_LLM_API_URL=http://<host>:8003/v1
export ALEMS_TRT_LLM_ENGINE_URL=http://<host>:8003
```

Replace `<host>` with the machine's LAN IP address.
See [Administrator Guide](../guides/administrator-guide.md) for the
complete `~/.alemsrc` reference.

## Energy reader plugins

Energy reader plugins add hardware-specific power measurement backends.
Install only the plugin that matches your hardware.

| Plugin | Platform | Reader | Status |
|---|---|---|---|
| alems-plugin-dcgm | NVIDIA DGX, GN100 (aarch64) | DCGM field 156 | built-in (GN100) |
| alems-plugin-nvml | NVIDIA discrete GPU (x86) | NVML energy counter | built-in |
| alems-plugin-rapl | Intel/AMD x86 Linux | MSR RAPL | built-in |
| alems-plugin-powermetrics | Apple Silicon (Darwin) | powermetrics | built-in |
| alems-plugin-rocm | AMD GPU | ROCm SMI | planned |
| alems-plugin-windows-energy | Windows x86/ARM | RAPL via WMI | planned |
| alems-plugin-mlx | Apple Silicon | MLX energy API | planned |

Built-in readers ship with `alems-platform` core and do not require
separate installation.
Planned plugins are community contributions — see
[Contributing a Plugin](#contributing-a-plugin) below.

## Task plugins

Task plugins add new experiment task types beyond the built-in
calculator, coding, and reasoning tasks.

| Plugin | Tasks added | Status |
|---|---|---|
| alems-plugin-tasks-vision | image captioning, VQA | planned |
| alems-plugin-tasks-audio | speech transcription | planned |
| alems-plugin-tasks-code | code generation benchmarks | planned |

## Tool provider plugins

Tool provider plugins add new tools available to agentic workflows.

| Plugin | Tools added | Status |
|---|---|---|
| alems-plugin-tools-search | web search via SerpAPI | planned |
| alems-plugin-tools-sql | SQL query execution | planned |
| alems-plugin-tools-rag | RAG pipeline integration | planned |

## Contributing a plugin

Any researcher or developer can write an A-LEMS plugin without touching
core A-LEMS code.
The entry point system is the only integration point.

**Minimum requirements for a serving engine plugin:**

1. Three entry point groups registered in `pyproject.toml`
2. Adapter inherits `CapabilityDiscoveryMixin` from `core.serving.discovery`
3. Fragment exports a `fragment` dict with structural defaults
4. Preflight exports a `check(config)` function that uses `config["base_url"]` — no hardcoded URLs
5. 9 test cases passing (use `tests/test_model_fragment_merge.py` as template)
6. Verified on at least one physical machine

**To get listed in this catalog:**

Open a pull request at
`https://github.com/deepakpanigrahy03/alems-platform` with:

- Your plugin package in `alems-plugin-<name>/`
- An entry added to this file (`reference/plugin-catalog.md`)
- An entry added to `docs-src/catalog.yaml`
- Test output showing all entry point groups registered
- The machine it was verified on

See [Serving Engine Adapters](../developer/serving-engine-adapters.md)
for the full plugin architecture and
[Plugin Architecture](../developer/plugin-packaging.md) for the
general plugin system.
