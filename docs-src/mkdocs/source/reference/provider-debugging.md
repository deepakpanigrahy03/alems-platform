# Provider and Model Debugging

A-LEMS supports 16 providers across text generation, text-to-speech,
speech-to-text, and voice cloning. This page covers how to test
connectivity, list available models, and diagnose provider failures.

---

## Configured Providers

| Provider | Type | Transport | Key / URL |
|---|---|---|---|
| `vllm_remote` | text generation | remote HTTP | `ALEMS_VLLM_REMOTE_URL` |
| `vllm_local` | text generation | loopback HTTP | none |
| `ollama_local` | text generation | loopback HTTP | none |
| `ollama_remote` | text generation | remote HTTP | server URL |
| `llama_cpp` | text generation | in-process | model file path |
| `groq` | text generation | internet | `GROQ_API_KEY` |
| `openai` | text generation | internet | `OPENAI_API_KEY` |
| `anthropic` | text generation | internet | `ANTHROPIC_API_KEY` |
| `gemini` | text generation | internet | `GEMINI_API_KEY` |
| `deepseek_cloud` | text generation | internet | `DEEPSEEK_API_KEY` |
| `nvidia_nim` | text generation | internet | `NVIDIA_API_KEY` |
| `kokoro` | TTS | local process | none |
| `indic_parler` | TTS | local process | none |
| `indic_f5` | voice clone | local process | none |
| `faster_whisper` | STT | local process | none |

Keys are set in `~/.alemsrc`. The `ALEMS_VLLM_REMOTE_URL` points to
the vllm server address (e.g. `http://100.84.85.2:8000/v1`).

---

## List Models for a Provider

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 -c "
import sys; sys.path.insert(0, '.')
from core.config.model_config import ModelConfig
cfg = ModelConfig()
for p in cfg.list_providers():
    models = cfg.list_models(p)
    print(f'{p}: {[m[\"model_id\"] for m in models]}')
"
```

For one provider:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from core.config.model_config import ModelConfig
cfg = ModelConfig()
for m in cfg.list_models('groq'):
    print(m['model_id'], m.get('name',''))
"
```

---

## Test Provider Connectivity

Use `test_llm_setup.py` to test one or all providers:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 core/execution/tests/test_llm_setup.py --provider groq

# Test all configured providers
python3 core/execution/tests/test_llm_setup.py --provider all --verbose
```

Output shows `OK` or an error with the failure reason per provider.

---

## Run a Single Preflight Check

`preflight` runs automatically before each experiment. To run it manually
against a provider to confirm it is reachable before starting a long run:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 -c "
import sys; sys.path.insert(0, '.')
from core.execution.harness import ExperimentHarness
from core.utils.preflight import preflight
harness = ExperimentHarness()
preflight(harness, 'groq')
print('preflight passed')
"
```

---

## Check vllm Server

```bash
# Is the server running and reachable?
curl -s http://100.84.85.2:8000/v1/models | python3 -m json.tool | head -20

# What models are loaded?
curl -s http://100.84.85.2:8000/v1/models | \
  python3 -c "import sys,json; [print(m['id']) for m in json.load(sys.stdin)['data']]"

# From within Python
python3 -c "
import sys; sys.path.insert(0, '.')
import os
url = os.environ.get('ALEMS_VLLM_REMOTE_URL', 'http://100.84.85.2:8000/v1')
print('ALEMS_VLLM_REMOTE_URL:', url)
import urllib.request
resp = urllib.request.urlopen(f'{url}/models', timeout=5)
import json
data = json.loads(resp.read())
print('Models:', [m['id'] for m in data['data']])
"
```

---

## Run a Dry-Run Experiment

Test a provider with one repetition of the simplest task before committing
to a full experiment:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 1 \
  --provider groq \
  --workflow-mode linear \
  --experiment-type debug \
  --experiment-goal "connectivity test" \
  --verbose
```

Add `--save-db` to write the result to the database.

---

## List Available Tasks

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 core/execution/tests/run_experiment.py --list-tasks
```

65 tasks across: arithmetic, reasoning, code, QA, summarization,
classification, NER, TTS, STT, voice cloning, tool chains, multi-step
agentic, multilingual.

---

## Common Provider Failures

**`No models found for provider`**

The provider name does not match any key in `models.yaml`, or `available: false`
is set for all its models. Check:

```bash
grep -A5 "^  groq:" ~/mydrive/alems-platform/config/models.yaml
```

**`API key not set`**

The key variable is not in `~/.alemsrc` or was not sourced:

```bash
grep "GROQ\|OPENAI\|ANTHROPIC\|NVIDIA\|GEMINI" ~/.alemsrc
source ~/.alemsrc
```

**`Connection refused` on vllm_remote**

The vllm server at `ALEMS_VLLM_REMOTE_URL` is not running or not reachable:

```bash
echo $ALEMS_VLLM_REMOTE_URL
curl -s --connect-timeout 3 \
  "${ALEMS_VLLM_REMOTE_URL}/models" || echo "server unreachable"
```

**`Failed to load configs`**

`get_model_config_v2` returned None. The provider+model combination
is not in `models.yaml`. Check the provider's models list:

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from core.config.model_config import ModelConfig
cfg = ModelConfig()
print(cfg.list_models('nvidia_nim'))
"
```

---

## Hardware Status During a Run

To see energy reader state and platform detection during a run, set verbose:

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 1 \
  --provider groq \
  --verbose
```

Verbose output shows per-pair hardware counters, energy values, and reader
state for each repetition.
