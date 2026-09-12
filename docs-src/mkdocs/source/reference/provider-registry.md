# Provider Registry

A-LEMS supports 16 providers across text generation, text-to-speech,
speech-to-text, and voice cloning. All providers are configured in
`config/models.yaml`. No provider-specific code exists outside the
model factory and adapter classes.

---

## Text Generation Providers

| Provider | Transport | API Key Variable | Base URL |
|---|---|---|---|
| `vllm_remote` | remote HTTP | `ALEMS_VLLM_REMOTE_URL` | your server |
| `vllm_local` | loopback HTTP | none | `http://localhost:8000` |
| `ollama_local` | loopback HTTP | none | `http://localhost:11434` |
| `ollama_remote` | remote HTTP | none | your server |
| `llama_cpp` | in-process | none | model file path |
| `groq` | internet | `GROQ_API_KEY` | api.groq.com |
| `openai` | internet | `OPENAI_API_KEY` | api.openai.com |
| `anthropic` | internet | `ANTHROPIC_API_KEY` | api.anthropic.com |
| `gemini` | internet | `GEMINI_API_KEY` | generativelanguage.googleapis.com |
| `deepseek_cloud` | internet | `DEEPSEEK_API_KEY` | api.deepseek.com |
| `nvidia_nim` | internet | `NVIDIA_API_KEY` | integrate.api.nvidia.com |

## Speech Providers

| Provider | Type | Transport | Key |
|---|---|---|---|
| `kokoro` | TTS | local process | none |
| `indic_parler` | TTS | local process | none |
| `indic_f5` | Voice clone | local process | none |
| `faster_whisper` | STT | local process | none |

---

## Configured Models

### vllm_remote / vllm_local

| Model ID | Name |
|---|---|
| `Mistral-7B-Instruct-v0.3` | Mistral 7B Instruct |
| `Qwen3-32B-AWQ` | Qwen3 32B AWQ |
| `Qwen3-30B-A3B` | Qwen3 30B MoE |
| `sarvam-m` | Sarvam M |
| `Qwen2.5-Coder-32B` | Qwen2.5 Coder 32B |

### groq

| Model ID | Name |
|---|---|
| `llama-3.3-70b-versatile` | Llama 3.3 70B |
| `llama-3.1-8b-instant` | Llama 3.1 8B Instant |
| `mixtral-8x7b-32768` | Mixtral 8x7B |
| `gemma2-9b-it` | Gemma2 9B |

### nvidia_nim

| Model ID | Name |
|---|---|
| `meta/llama-3.3-70b-instruct` | Llama 3.3 70B |
| `meta/llama-3.1-8b-instruct` | Llama 3.1 8B |
| `nvidia/kimi-k2.6` | Kimi K2.6 Reasoning |
| `nvidia/nemotron-ultra-550b` | Nemotron Ultra 550B |
| `nvidia/nemotron-super-49b` | Nemotron Super 49B |

### llama_cpp

| Model ID | Name |
|---|---|
| `tinyllama-1b-gguf` | TinyLlama 1B GGUF |
| `phi-2-gguf` | Phi-2 GGUF |
| `llama-3.2-3b-gguf` | Llama 3.2 3B GGUF |

---

## Provider Properties

| Property | Values | Meaning |
|---|---|---|
| `is_local` | true / false | Inference runs on this host |
| `energy_side` | full / client_only / remote_measured | What energy is captured |
| `transport` | inprocess / loopback_http / remote_http | Network path |
| `openai_compat` | true / false | Speaks /chat/completions format |
| `cost_class` | free / paid / metered | Cost model |
| `tools_supported` | true / false | Function/tool calling supported |

---

## Adding a Provider

Add an entry to `config/models.yaml` under `providers:`. The model
factory reads this file at runtime. No code changes are required for
providers that use the OpenAI-compatible `/chat/completions` API.

See `developer/adding-a-provider.md` for the full process.

---

## Testing Provider Connectivity

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

# Test all configured providers
python3 core/execution/tests/test_llm_setup.py --provider all --verbose

# Test one provider
python3 core/execution/tests/test_llm_setup.py --provider groq

# Check vllm server
curl -s "${ALEMS_VLLM_REMOTE_URL}/models" | python3 -m json.tool | head -20
```
