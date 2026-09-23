# How to Add a Provider

## Step 1 — Add to models.yaml

```yaml
providers:
  new_provider:
    provider_meta:
      is_local:            false         # true only if runs on THIS host
      access_method:       api_http      # api_http | direct_file | local_process
      network_type:        internet      # none | loopback | internet
      captures_network_io: true
      energy_side:         client_only   # full | client_only
      openai_compat:       true          # speaks /chat/completions format?
      base_url:            https://api.newprovider.com/v1
      api_key_env:         NEW_PROVIDER_API_KEY
      cost_class:          paid          # free | paid | metered
      priority:            8
      rate_limit_tpm:      null
    defaults:
      temperature: 0.7
      max_tokens: 2048
    models:
      - id: model-name
        name: Model Human Name
        tasks: [text-generation]
        modes: [linear, agentic]
        tools_supported: true
```

## Step 2 — Create Adapter (if needed)

If `openai_compat: true` → no adapter needed. `OpenAICompatAdapter` handles it.

If SDK-based (like anthropic, gemini):

```python
# core/execution/adapters/new_provider.py
from core.execution.adapters.base import TextGenABC

class NewProviderAdapter(TextGenABC):
    def __init__(self, provider_config, model_config):
        super().__init__(provider_config, model_config)
        self._api_key_env = provider_config.get("api_key_env", "")

    def get_name(self): return f"NewProviderAdapter({self.model_id})"

    def is_available(self):
        import os
        return bool(os.getenv(self._api_key_env))

    def call(self, prompt, temperature):
        # implement API call
        # return standard result dict (see base.py TextGenABC.call docstring)
        pass

    def _error_result(self, error_msg, preprocess_ms):
        phase_metrics = self._make_phase_metrics(
            total_time_ms=preprocess_ms, preprocess_ms=preprocess_ms,
            non_local_ms=0.0, local_compute_ms=0.0, postprocess_ms=0.0,
            app_throughput_kbps=0.0, cpu_percent_during_wait=0.0,
        )
        return {
            "content": f"Error: {error_msg}",
            "tokens": {"prompt": 0, "completion": 0, "total": 0},
            "total_time_ms": preprocess_ms,
            "phase_metrics": phase_metrics,
            "bytes_sent": 0, "bytes_recv": 0, "tcp_retransmits": 0,
        }
```

## Step 3 — Register in model_factory.py

```python
# In _NON_COMPAT_CLOUD dict:
from core.execution.adapters.new_provider import NewProviderAdapter
_NON_COMPAT_CLOUD = {
    "anthropic":    AnthropicAdapter,
    "gemini":       GeminiAdapter,
    "new_provider": NewProviderAdapter,  # add this
}
```

## Step 4 — Test

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from core.execution.model_factory import ModelFactory
providers = ModelFactory.list_providers()
print('new_provider' in providers)
"

python3 -c "
from dotenv import load_dotenv; load_dotenv()
from core.config_loader import ConfigLoader
from core.execution.linear import LinearExecutor
config = ConfigLoader()
cfg = config.get_model_config_v2('new_provider', 'model-name')
ex = LinearExecutor(cfg)
result = ex.execute('What is 2+2?')
print('tokens:', result.get('tokens'))
print('error:', result.get('error'))
"
```

## Step 5 — Regression

```bash
bash scripts/test_provenance.sh
bash scripts/test_runs_regression.sh 2>&1 | grep FAIL
```

## Fragment-based providers (serving engine plugins)

The steps above describe adding a cloud or API provider manually.
For local serving engine providers (vLLM, SGLang, llama.cpp, Colibri,
TRT-LLM), a different path applies — the plugin owns the provider
defaults and registers them automatically via the
`alems.models.fragments` entry point group.

You do not write a provider block in models.yaml for these engines.
The fragment supplies all structural defaults at startup.
You only write what differs from the fragment defaults.

### What the fragment supplies

When `alems-plugin-sglang` is installed and `models_loader._load()`
runs, the fragment fills these keys automatically:

```
is_local, access_method, network_type, captures_network_io,
energy_side, openai_compat, base_url (from ALEMS_SGLANG_API_URL),
api_key_env, cost_class, priority, rate_limit_tpm,
execution_site, transport, remote_energy_available,
serving_engine_defaults
```

### What you write in models.yaml

Only the model list and any researcher overrides:

```yaml
providers:
  sglang_remote:
    models:
      - id: Mistral-7B-Instruct-v0.3
        name: Mistral 7B (GN100 SGLang)
      - id: Llama-3.2-3B-Instruct
        name: Llama 3.2 3B (GN100 SGLang)
```

If you need to override `base_url` for a specific lab setup, add it
and it beats the fragment value:

```yaml
providers:
  sglang_remote:
    base_url: http://my-custom-host:30000/v1
    models:
      - id: Mistral-7B-Instruct-v0.3
        name: Mistral 7B
```

### The override precedence

```
fragment default < models.yaml value < ~/.alemsrc env var resolution < experiment YAML
```

models.yaml always beats the fragment.
`~/.alemsrc` beats models.yaml only through the fragment's
`base_url_env` directive — which is resolved before the merge, so
models.yaml still wins if it has an explicit value.

### Verifying fragment registration

After installing a plugin, confirm the fragment loaded:

```bash
python3 -c "
import logging; logging.basicConfig(level=logging.INFO)
from core.models_loader import get_provider
p = get_provider('sglang_remote')
print(p['provider_meta']['is_local'])
print(p['provider_meta']['openai_compat'])
"
```

The INFO log will show which entry point supplied each provider.
If `get_provider` returns None, the plugin is not installed in the
active venv — install with `venv/bin/pip install -e alems-plugin-sglang/`.

See `developer/serving-engine-adapters.md` for the full plugin
architecture including adapter, fragment, and preflight entry points.
