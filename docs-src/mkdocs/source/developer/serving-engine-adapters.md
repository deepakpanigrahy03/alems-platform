# Serving Engine Adapters

A serving engine adapter connects A-LEMS to a running LLM inference
server and retrieves telemetry — KV cache hit rate, queue depth, token
throughput — that the core energy measurement chain cannot see on its own.
Energy tells you how much power the GPU drew.
The serving engine adapter tells you why: was the cache cold, was the
queue saturated, how many tokens per second was the engine producing?
Together they give you the full picture.

Adapters live in separate pip-installable plugin packages — one per
engine family. Installing the package is sufficient; A-LEMS discovers
it automatically at startup via Python entry points.

## The three things a plugin owns

Every serving engine plugin registers three entry points:

**`alems.engines.serving`** — the adapter class that probes the engine
and parses its telemetry.
A-LEMS uses this to populate `serving_runtime_snapshots` rows during
a run.

**`alems.models.fragments`** — a provider defaults dict that
`models_loader` merges into the model registry at startup.
This is how the plugin tells A-LEMS "here is what vllm_remote looks
like structurally — is_local, openai_compat, network_type — without
the researcher having to write boilerplate in models.yaml."

**`alems.preflight.checks`** — a health check function that runs before
each experiment.
When the engine is unreachable, this prints an actionable error message
with the exact command to start the server, then exits cleanly.
No experiment runs against a dead engine.

## Supported engines

| Plugin package | Provider name | Entry point key |
|---|---|---|
| alems-plugin-vllm | vllm_remote | vllm |
| alems-plugin-sglang | sglang_remote | sglang |
| alems-plugin-llama-cpp | llamacpp_remote | llama_cpp |
| alems-plugin-tensorrt-llm | tensorrt_llm_remote | tensorrt_llm |
| alems-plugin-colibri | colibri_remote | colibri |

## How capability discovery works

Every adapter inherits `CapabilityDiscoveryMixin` and runs a four-probe
sequence at startup against the live engine:

```
GET /v1/models       → engine alive (all engines)
GET /metrics         → Prometheus text format (vLLM, SGLang, llama-server)
GET /health          → JSON health endpoint (Colibri)
GET /prometheus/metrics → non-standard path (TRT-LLM)
```

The mixin reads actual Prometheus metric family prefixes from the
response content.
It never assumes capability from the engine type — it reads what the
engine actually exposes.
If `/metrics` returns 404, `kv_cache_metrics` and `queue_metrics` are
both set to False.
The adapter degrades gracefully: parse methods guard with
`if not self._caps.<cap>: return empty dataclass` and no rows are
written to `serving_runtime_snapshots`.

This is why llama-cpp-python produces zero rows — its `/metrics`
endpoint returns 404.
The C++ `llama-server` binary with `--metrics` flag produces rows
correctly because its `/metrics` returns valid Prometheus output.
The same adapter code handles both variants transparently.

## Configuration resolution

Every adapter resolves its endpoint through a three-layer chain:

```
1. experiment YAML serving_engine: block   (highest priority)
2. config/adapters.yaml fleet defaults
3. ALEMS_<ENGINE>_ENGINE_URL in ~/.alemsrc
```

The engine URL (used for adapter probes) is distinct from the API base
URL (used for inference requests).
They are never the same variable and must never be aliased:

```
ALEMS_VLLM_API_URL=http://100.84.85.2:8000/v1    # inference, includes /v1
ALEMS_VLLM_ENGINE_URL=http://100.84.85.2:8000     # adapter probes, no /v1
```

Setting `endpoint: null` in `adapters.yaml` forces the value to come
from the env var or experiment YAML.
No URL is ever hardcoded in plugin code or fleet config.

## The model fragment and the four-layer override

When a serving engine plugin is installed, its fragment supplies
provider defaults to `models_loader` at startup.
The merge order is:

```
Layer 1 — fragment default      (plugin ships it, lowest priority)
Layer 2 — models.yaml block     (lab-wide researcher config)
Layer 3 — ~/.alemsrc env var    (this machine's value)
Layer 4 — experiment YAML       (per-run override, highest priority)
```

Each layer only needs to state what differs from the layer below.
A researcher on a new machine adds two lines to `~/.alemsrc` and runs
experiments.
They never touch plugin code.

`models.yaml` always wins over the fragment on any key collision.
The fragment is the fallback, never the override.
Fragment env vars (`base_url_env`) are resolved before the merge, so
even env var values cannot override a `models.yaml` explicit value.

One key that survives resolution unchanged is `api_key_env`.
It is runtime metadata for the inference client, not a resolution
directive.
The merge logic excludes it from `_resolve_env_keys` explicitly.

## What goes in models.yaml vs the fragment

The fragment owns structural defaults that never change per machine:
`is_local`, `openai_compat`, `network_type`, `energy_side`,
`execution_site`, `transport`, `cost_class`, `priority`.

The researcher owns what changes per deployment:
- `base_url` — where the engine is on this machine or cluster
- `models` — which models are loaded and their display names

A minimal models.yaml entry for a fragment-backed provider is:

```yaml
providers:
  sglang_remote:
    models:
      - id: Mistral-7B-Instruct-v0.3
        name: Mistral 7B (GN100 SGLang)
```

Everything else comes from the fragment.
If you omit the block entirely, the fragment supplies all defaults and
the model list is empty — the adapter will discover models from
`GET /v1/models` at runtime but the registry will have no named entries
for experiment YAML to reference.

## Writing a new engine plugin

Create a package with this layout:

```
alems-plugin-myengine/
  alems_plugin_myengine/
    __init__.py
    adapter.py          # inherits CapabilityDiscoveryMixin
    models_fragment.py  # exports fragment dict
    preflight.py        # exports check(config) function
  pyproject.toml
```

Register all three entry point groups in `pyproject.toml`:

```toml
[project.entry-points."alems.engines.serving"]
myengine = "alems_plugin_myengine.adapter:MyEngineAdapter"

[project.entry-points."alems.models.fragments"]
myengine = "alems_plugin_myengine.models_fragment:fragment"

[project.entry-points."alems.preflight.checks"]
myengine_remote = "alems_plugin_myengine.preflight:check"
```

The adapter must inherit `CapabilityDiscoveryMixin` from
`core.serving.discovery` and call `AdapterConfig.resolve()` from
`core.serving.adapter_config` in `__init__`.
See `alems-plugin-vllm` as the reference implementation — its parse
logic is the most complete and its B4 verification is the most
thorough.

The fragment must export a module-level `fragment` dict keyed by
provider name.
Use `base_url_env` for the machine-specific URL directive.
Set `models: []` — runtime discovery handles the model list.

The preflight function receives the fully resolved config dict from
`models_loader.get_model()`.
`config["base_url"]` is already env-expanded.
No hardcoding of URLs in preflight code.

Install with:

```bash
venv/bin/pip install -e alems-plugin-myengine/
```

Verify all three entry point groups registered:

```bash
venv/bin/python3 -c "
from importlib.metadata import entry_points
for g in ['alems.engines.serving','alems.models.fragments','alems.preflight.checks']:
    print(g, '->', [ep.name for ep in entry_points(group=g)])
"
```

## TRT-LLM: NGC container required

TRT-LLM cannot be installed via pip on a bare system.
The pip wheel's `libth_common.so` requires an NGC-patched torch build
that is not available on PyPI.
The correct deployment path is the NGC container:

```bash
sudo docker login nvcr.io
# Username: $oauthtoken
# Password: <NGC API key from ngc.nvidia.com/setup/api-key>

sudo docker run --gpus all -p 8003:8000 \
  -v /opt/ai-stack/models:/models \
  nvcr.io/nvidia/tensorrt-llm:0.17.0 \
  trtllm-serve mistralai/Mistral-7B-Instruct-v0.3 --port 8000
```

Set `ALEMS_TRT_LLM_API_URL=http://<host>:8003/v1` and
`ALEMS_TRT_LLM_ENGINE_URL=http://<host>:8003` in `~/.alemsrc`.
The adapter and fragment are code-complete and activate automatically
once the container is running.

## Colibri: supported model families

Colibri v1.12.0 supports GLM-5.2, Kimi K3, and DeepSeek V4.1 Flash.
Mistral-7B is not supported (`unsupported model_type: mistral`).
The adapter uses `GET /health` for telemetry rather than
`GET /metrics` — Colibri does not expose a Prometheus endpoint.
`ColibriAdapter` overrides `_caps_from_probes()` to set
`queue_metrics=True` from the `/health` JSON response directly.
The adapter and fragment activate automatically once a supported model
is downloaded and the server is running.

## Cross-references

- `core/serving/discovery.py` — `CapabilityDiscoveryMixin`
- `core/serving/adapter_config.py` — three-layer config resolution
- `config/adapters.yaml` — fleet-level engine defaults
- `core/models_loader.py` — fragment discovery and merge
- `developer/plugin-packaging.md` — general plugin architecture
- `developer/adding-a-provider.md` — models.yaml provider registration
- `guides/administrator-guide.md` — fleet setup and `~/.alemsrc`
- `research/gpu-energy.md` — GPU energy backends (DCGM, NVML, MSR)
