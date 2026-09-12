---
topic: "API Keys"
audience: user
status: current
last_validated: 2026-09-11
---

# API Keys

A-LEMS runs experiments against local models (via vllm) and cloud providers (OpenAI, Anthropic, Groq, NVIDIA NIM). Keys and endpoint URLs go in one place only: `~/.alemsrc`. They are never stored in the repository, never in YAML config files, and never in the database.

---

## Where Keys Live

The installer creates `~/.alemsrc` in your home directory during setup. Open it in any editor:

```bash
nano ~/.alemsrc
```

Add your keys to the existing file. Do not create a new file. The installer already wrote `ALEMS_DATA_ROOT` and `ALEMS_ENV` there; add API keys alongside them.

---

## Provider Keys

| Provider | Environment Variable | Where to Get It |
|---|---|---|
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | [build.nvidia.com](https://build.nvidia.com) |
| Groq | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| vllm remote | `ALEMS_VLLM_REMOTE_URL` | set to your server address |

You do not need all providers. Add only the ones you plan to use. A-LEMS skips providers with no key configured.

---

## Example ~/.alemsrc

```bash
# Written by install.sh — edit to add API keys
export ALEMS_DATA_ROOT=/mnt/alems-data
export ALEMS_MODELS_DIR=/home/yourname/models

# Cloud providers — add keys for providers you use
export NVIDIA_NIM_API_KEY=nvapi-...
export GROQ_API_KEY=gsk_...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# Local vllm server — set if running vllm on another machine
export ALEMS_VLLM_REMOTE_URL=http://100.84.85.2:8000/v1
```

After editing, reload your environment:

```bash
source ~/.alemsrc
```

---

## Running a Local Model

For local inference, A-LEMS uses vllm. Start vllm on any machine (including the same one):

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

Then set `ALEMS_VLLM_REMOTE_URL` to point to it:

```bash
export ALEMS_VLLM_REMOTE_URL=http://localhost:8000/v1
```

If vllm is on a different machine, use that machine's Tailscale or LAN address instead of `localhost`. The GN100 serves models over its Tailscale address by default.

---

## Verifying Provider Connectivity

```bash
alems dev status
```

The status output shows each configured provider with a connectivity check:

```
Providers:    vllm_remote ✓   groq ✓   openai ✗   anthropic ✗
```

A `✗` means either the key is missing from `~/.alemsrc` or the provider API is unreachable. For vllm, a `✗` means the server at `ALEMS_VLLM_REMOTE_URL` did not respond.

For a detailed connectivity check with error messages:

```bash
python -m core.execution.tests.test_llm_setup --provider all --verbose
```

Each provider prints either `OK` or an error with the reason: wrong key format, network unreachable, model not found, or quota exceeded.

---

## Key Security

`~/.alemsrc` is a shell file in your home directory. It is not encrypted. Do not put it in a shared location. Do not commit it to any repository. The `.gitignore` in `alems-platform` excludes `.alemsrc` and `.alems-env`, but your home directory `~/.alemsrc` is outside the repository and has no such protection.

If a key is compromised, rotate it in the provider dashboard immediately and update `~/.alemsrc`.
