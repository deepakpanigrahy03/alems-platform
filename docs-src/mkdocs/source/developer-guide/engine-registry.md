# A-LEMS Serving Engine Registry

## Overview

Before this change, `ModelFactory` in `core/execution/model_factory.py`
contained hardcoded dispatch tables — `_NON_COMPAT_CLOUD` and `_MEDIA_ADAPTERS`
— that mapped provider strings to adapter classes. Adding a new serving engine
meant editing the factory directly and importing the new class at the top of
the file. Every new adapter touched the same file, risking regressions across
all providers.

The engine registry solves this the same way the reader registry solved it for
hardware readers: each adapter declares its own identity, and the factory asks
the registry for the right class rather than containing the mapping itself.

This document covers SPEC 35B (serving engine registry, Phase 2). The reader
registry is documented separately in adapter-registry.md.

## How Engine Selection Differs from Reader Selection

Reader selection uses capability probing — `can_handle(caps)` is called on
every registered reader and the one with the lowest `PRIORITY` among eligible
readers wins. This is needed because the platform hardware determines which
reader is correct.

Engine selection is simpler. The provider configuration already names the
engine family — `llama_cpp`, `openai_compat`, `anthropic`. There is no
probing and no priority ordering. The registry does a direct key lookup:
`text_registry.get("openai_compat")` returns `OpenAICompatAdapter`. This
is why the engine registry adds a `get(key)` method to `AdapterRegistry`
rather than using `select(caps)`.

## ENGINE_TYPE vs Provider ID

A critical distinction: `ENGINE_TYPE` identifies the adapter family, not
the specific provider endpoint.

`OpenAICompatAdapter` has `ENGINE_TYPE = "openai_compat"`. It handles
OpenAI, Groq, NIM, vllm_remote, and Ollama — all of which speak the same
HTTP format. The specific endpoint URL, API key, and model ID come from
the provider configuration in `models.json`, not from `ENGINE_TYPE`. This
means adding a new OpenAI-compatible provider (say, a new inference service)
requires only a new entry in `models.json` — no new adapter class and no
registry change.

`ENGINE_TYPE` changes only when the wire protocol or SDK changes. A new
adapter class is needed only when a provider speaks a fundamentally
different format.

## Runtime Lifecycle

### Registration

`model_factory.py` imports `bootstrap.py` at module load time and calls
`register_all_adapters()` immediately. This populates `text_registry` and
`media_registry` before any `ModelFactory.get_adapter()` call.

Each adapter class is imported in a local `try/except` block. Adapters with
heavy optional dependencies (torch, llama-cpp-python, google-generativeai)
that are not installed on the current machine are skipped with a debug log.
The factory never sees an import error from an adapter it cannot use.

### Lookup

When `ModelFactory._resolve_http()` is called, it determines the `ENGINE_TYPE`
from the provider metadata (`openai_compat` flag or `provider_id`) and calls:

```python
adapter_cls = text_registry.get(engine_type)
return adapter_cls(meta, flat_config)
```

If the registry raises `KeyError` (engine not registered), the factory logs
a WARNING and falls through to the existing legacy dispatch — `_NON_COMPAT_CLOUD`
and `_MEDIA_ADAPTERS` dicts — which remain in place for backward compatibility.

### Measurement boundary

The registry changes how the adapter class is resolved. It does not change
when or how energy is measured. The harness in `core/execution/harness.py`
reads energy before calling `adapter.call()` and after. The adapter is
inside the measurement boundary but has no access to energy readers or the
`runs` table. This is unchanged by the registry.

## Where the Registry Lives

`text_registry` and `media_registry` are module-level `AdapterRegistry`
instances in `core/execution/adapters/bootstrap.py`. They live in Python
process memory, populated once at `model_factory.py` import time. There is
no persistent registry state and no database footprint.

## Platform Coverage

The engine registry applies to all platforms equally. Serving engine
selection is not platform-dependent — the same adapter classes run on
GN100, Lenovo, and AMD. Platform differences in inference performance or
energy cost are captured in measurement data, not in adapter selection logic.

| Adapter | ENGINE_TYPE | Family | Covers |
|---|---|---|---|
| OpenAICompatAdapter | openai_compat | text | OpenAI, Groq, NIM, vllm_remote, Ollama |
| AnthropicAdapter | anthropic | text | Anthropic Claude SDK |
| LlamaCppAdapter | llama_cpp | text | Local llama-cpp-python GGUF |
| GeminiAdapter | gemini | text | Google Gemini SDK |
| KokoroAdapter | kokoro | media | Kokoro TTS |
| FasterWhisperAdapter | faster_whisper | media | Faster Whisper STT |
| IndicF5Adapter | indic_f5 | media | Indic F5 TTS |
| IndicParlerAdapter | indic_parler | media | Indic Parler TTS |

## Adding a New Serving Engine

A new serving engine requires exactly two changes and zero changes to
`model_factory.py`.

**Step 1.** Create the adapter file subclassing `TextGenABC` or `MediaABC`.
Declare `ENGINE_TYPE` and `METHOD_ID` (both set to the same string):

```python
from core.execution.adapters.base import TextGenABC

class OllamaDirectAdapter(TextGenABC):
    """Adapter for Ollama native API (non-OpenAI-compat format)."""

    ENGINE_TYPE: str = "ollama_direct"
    METHOD_ID:   str = "ollama_direct"

    def call(self, prompt: str, temperature: float) -> dict:
        ...

    def is_available(self) -> bool:
        ...

    def get_name(self) -> str:
        return "OllamaDirectAdapter"
```

**Step 2.** Register it in `bootstrap.py`:

```python
def register_text_adapters() -> None:
    ...
    try:
        from core.execution.adapters.ollama_direct import OllamaDirectAdapter
        _safe_register(text_registry, OllamaDirectAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[text_engine]: OllamaDirectAdapter not importable: %s", exc)
```

No changes to `model_factory.py` required. The registry finds it
automatically at next startup.

## Adding an External Plugin Engine

An external plugin registers via `pyproject.toml`:

```toml
[project.entry-points."alems.engines.text"]
ollama_direct = "alems_engine_ollama.adapter:OllamaDirectAdapter"
```

`pip install alems-engine-ollama` installs the plugin. At next startup,
entry_points discovery finds the class and registers it. No core file changes
required.

## Failure Policy

**Unknown ENGINE_TYPE.** `registry.get()` raises `KeyError`. The factory
logs a WARNING and falls through to the legacy `_NON_COMPAT_CLOUD` and
`_MEDIA_ADAPTERS` dicts. If the legacy dispatch also fails, `ValueError`
is raised as before. No silent failures.

**Duplicate ENGINE_TYPE.** `DuplicateRegistrationError` is raised at
registration time, naming both conflicting classes. This catches two
plugins claiming the same engine family before any request is served.

## Verification

```bash
# Verify engine registry resolves all built-in adapters
python3 -c "
from core.execution.adapters.bootstrap import (
    register_all_adapters, text_registry, media_registry
)
register_all_adapters()
for engine_type in ['openai_compat', 'anthropic', 'llama_cpp', 'gemini']:
    cls = text_registry.get(engine_type)
    print(f'text  {engine_type}: {cls.__name__}')
for engine_type in ['kokoro', 'faster_whisper', 'indic_f5', 'indic_parler']:
    cls = media_registry.get(engine_type)
    print(f'media {engine_type}: {cls.__name__}')
print('PASS')
"

# Run unit tests
python3 -m pytest tests/test_engine_registry.py -v

# Full measurement run to verify unchanged behavior
python3 ~/mydrive/alems-platform/core/execution/tests/run_experiment.py \
  --tasks tg_single_calc --repetitions 1 --provider llama_cpp \
  --experiment-type normal \
  --experiment-goal "35B regression" \
  --save-db 2>&1 | grep -E "registry resolved|energy_uj|ERROR"
```

## Known Limitations

**`get()` vs `select()` asymmetry.** The reader registry uses `select(caps)`
with `can_handle()` and `PRIORITY`. The engine registry uses `get(key)` with
direct string lookup. Both use the same `AdapterRegistry` class — `get()` was
added to `AdapterRegistry` in this phase. Future adapter families will use
whichever pattern fits their selection model.

**`_resolve_http()` ENGINE_TYPE derivation.** The engine type is currently
derived in `_resolve_http()` from `meta.get("openai_compat")` and
`provider_id`. This is a translation layer that exists because the provider
config does not yet have an explicit `engine_type` field. A future cleanup
could add `engine_type` directly to provider metadata in `models.json`,
making the derivation unnecessary.
