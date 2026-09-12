# API Keys

API keys and server URLs go in one place only: `~/.alemsrc`. They are never
stored in the repository, never in YAML config files, and never in the database.

`~/.alemsrc` is sourced automatically by every A-LEMS script. You do not need
to source it manually before running experiments.

---

## Where Keys Go

Open `~/.alemsrc` in any editor and add your keys:

```bash
nano ~/.alemsrc
```

The installer already wrote `ALEMS_DATA_ROOT` and `ALEMS_MODELS_DIR` here.
Add API keys alongside them.

After editing:

```bash
source ~/.alemsrc
```

---

## Provider Keys

| Provider | Variable | Where to get it |
|---|---|---|
| Groq | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| Google Gemini | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| NVIDIA NIM | `NVIDIA_API_KEY` | [build.nvidia.com](https://build.nvidia.com) |
| DeepSeek | `DEEPSEEK_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com) |
| vllm remote | `ALEMS_VLLM_REMOTE_URL` | set to your server address |

Local providers (ollama_local, llama_cpp, kokoro, indic_parler, indic_f5,
faster_whisper) require no API key. They need model files or a running server.

---

## Example ~/.alemsrc

```bash
# Written by install.sh
export ALEMS_DATA_ROOT=/mnt/alems-data
export ALEMS_MODELS_DIR=/home/dpani/models

# Cloud providers
export GROQ_API_KEY=gsk_...
export NVIDIA_API_KEY=nvapi-...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# Local vllm server
export ALEMS_VLLM_REMOTE_URL=http://100.84.85.2:8000/v1
```

---

## Verify Provider Connectivity

```bash
alems dev status
```

Shows each configured provider with a connectivity check. A provider with
no key set does not appear in the list.

For a detailed check per provider:

```bash
cd alems-platform && source venv/bin/activate

# Test one provider
python3 core/execution/tests/test_llm_setup.py --provider groq

# Test all providers
python3 core/execution/tests/test_llm_setup.py --provider all --verbose
```

Each provider prints `OK` or an error with the failure reason.

---

## vllm Remote Server

For a vllm server running on another machine, set `ALEMS_VLLM_REMOTE_URL`
to the server's address:

```bash
export ALEMS_VLLM_REMOTE_URL=http://100.84.85.2:8000/v1
```

Check what models are loaded on the server:

```bash
curl -s "${ALEMS_VLLM_REMOTE_URL}/models" | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data['data']:
    print(m['id'])
"
```

---

## Local Models (llama_cpp)

For llama_cpp, set `ALEMS_MODELS_DIR` to the directory containing your
GGUF model files:

```bash
export ALEMS_MODELS_DIR=/home/dpani/models
```

Model filenames are configured in `config/models.yaml` under the `llama_cpp`
provider.

---

## Security

`~/.alemsrc` is a plain text file in your home directory. Do not put it in
a shared location and do not commit it to any repository. The `.gitignore`
in `alems-platform` excludes `.alems-env` and any local config files, but
`~/.alemsrc` lives outside the repository and has no such protection.

If a key is exposed, rotate it immediately in the provider dashboard and
update `~/.alemsrc`.
