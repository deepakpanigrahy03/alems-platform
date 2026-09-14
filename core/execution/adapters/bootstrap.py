#!/usr/bin/env python3
"""
================================================================================
ENGINE BOOTSTRAP  —  core/execution/adapters/bootstrap.py
================================================================================

Purpose:
    Register all built-in serving engine adapter classes into their family
    registries. Called once at startup from model_factory.py before any
    adapter is resolved.

    Phase 2 (this file): explicit registration via local imports.
    Phase 6 (Spec 35E):  entry_points discovery replaces this file.

    Adding a new engine adapter:
        (a) Create the adapter class file subclassing TextGenABC or MediaABC.
        (b) Declare ENGINE_TYPE on the class.
        (c) Add one import + _safe_register() call below.
        No model_factory.py changes needed.

Import isolation:
    Every import is local to its try/except block.
    Heavy dependencies (torch, llama-cpp-python, google-generativeai) are
    never imported on machines where they are not installed.

Author: Deepak Panigrahy
Spec:   SPEC 35B, Phase 2
================================================================================
"""

import logging
from core.readers.registry import AdapterRegistry, DuplicateRegistrationError
from core.plugin_discovery import discover_plugins
from core.startup_banner import print_adapter_summary
from alems import __version__ as _CORE_VERSION
 
logger = logging.getLogger(__name__)
 
# ---------------------------------------------------------------------------
# Registry instances — one per adapter family.
# text_registry: ENGINE_TYPE -> TextGenABC subclass
# media_registry: ENGINE_TYPE -> MediaABC subclass
# ---------------------------------------------------------------------------
 
text_registry  = AdapterRegistry(family="text_engine")
media_registry = AdapterRegistry(family="media_engine")


# ---------------------------------------------------------------------------
# _safe_register: same pattern as reader bootstrap
# ---------------------------------------------------------------------------

def _safe_register(registry: AdapterRegistry, cls, config: dict = None) -> None:
    """
    Register cls, re-raising DuplicateRegistrationError (programming error)
    and logging all other exceptions as warnings.
 
    Args:
        registry: Target AdapterRegistry instance.
        cls:      Adapter class to register.
        config:   Validated plugin config dict from load_plugin_config().
                  None for built-in adapters.
    """
    try:
        registry.register(cls)
    except DuplicateRegistrationError:
        raise
    except Exception as exc:
        logger.warning(
            "bootstrap[%s]: failed to register %s: %s — skipping",
            registry._family,
            getattr(cls, "__name__", repr(cls)),
            exc,
        )


# ---------------------------------------------------------------------------
# Per-family registration
# ---------------------------------------------------------------------------

def register_text_adapters() -> None:
    """
    Register all built-in text generation adapters.

    ENGINE_TYPE assignments (SPEC 35B §4):
        openai_compat — OpenAI, Groq, NIM, vllm_remote, Ollama (all speak same format)
        anthropic     — Anthropic Claude SDK
        llama_cpp     — local llama-cpp-python GGUF
        gemini        — Google Gemini SDK
    """
    try:
        from core.execution.adapters.openai_compat import OpenAICompatAdapter
        _safe_register(text_registry, OpenAICompatAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[text_engine]: OpenAICompatAdapter not importable: %s", exc)

    try:
        from core.execution.adapters.anthropic import AnthropicAdapter
        _safe_register(text_registry, AnthropicAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[text_engine]: AnthropicAdapter not importable: %s", exc)

    try:
        from core.execution.adapters.llama_cpp import LlamaCppAdapter
        _safe_register(text_registry, LlamaCppAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[text_engine]: LlamaCppAdapter not importable: %s", exc)

    try:
        from core.execution.adapters.gemini import GeminiAdapter
        _safe_register(text_registry, GeminiAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[text_engine]: GeminiAdapter not importable: %s", exc)


def register_media_adapters() -> None:
    """
    Register all built-in media (TTS/STT) adapters.

    ENGINE_TYPE assignments (SPEC 35B §4):
        kokoro         — Kokoro TTS
        faster_whisper — Faster Whisper STT
        indic_f5       — Indic F5 TTS
        indic_parler   — Indic Parler TTS
    """
    try:
        from core.execution.adapters.kokoro import KokoroAdapter
        _safe_register(media_registry, KokoroAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[media_engine]: KokoroAdapter not importable: %s", exc)

    try:
        from core.execution.adapters.faster_whisper import FasterWhisperAdapter
        _safe_register(media_registry, FasterWhisperAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[media_engine]: FasterWhisperAdapter not importable: %s", exc)

    try:
        from core.execution.adapters.indic_f5 import IndicF5Adapter
        _safe_register(media_registry, IndicF5Adapter)
    except ImportError as exc:
        logger.debug("bootstrap[media_engine]: IndicF5Adapter not importable: %s", exc)

    try:
        from core.execution.adapters.indic_parler import IndicParlerAdapter
        _safe_register(media_registry, IndicParlerAdapter)
    except ImportError as exc:
        logger.debug("bootstrap[media_engine]: IndicParlerAdapter not importable: %s", exc)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def register_external_engine_plugins() -> None:
    """
    Discover and register externally pip-installed engines via entry_points.

    Engines select by exact ENGINE_TYPE string match (registry.get()),
    not can_handle()/PRIORITY, so an external engine plugin is usable
    the moment the provider config names its ENGINE_TYPE.
    """
    family_groups = {
        "text_engine":  ("alems.engines.text", text_registry),
        "media_engine": ("alems.engines.media", media_registry),
    }
    all_builtin = []
    all_external = []
    for family_name, (group, registry) in family_groups.items():
        builtin_before = list(registry.get_all().keys())
        names = discover_plugins(
            group=group,
            register_fn=lambda cls, cfg, r=registry: _safe_register(r, cls, cfg),
            core_version=_CORE_VERSION,
        )
        all_builtin.extend(builtin_before)
        all_external.extend(names)
        if names:
            logger.info(
                "bootstrap[%s]: %d external plugin(s) registered via entry_points",
                family_name, len(names),
            )
    print_adapter_summary("engines", all_builtin, all_external)
 
def register_all_adapters() -> None:
    """
    Register every built-in adapter family.
    Called once from model_factory.py before any adapter is resolved.
    Guard against double-registration: model_factory.py imports adapter
    classes at module level which can trigger this function more than once
    in the same process if multiple modules call it.
    """
    if not text_registry.is_empty() or not media_registry.is_empty():
        logger.debug("bootstrap: engine adapters already registered — skipping")
        return
    logger.info("bootstrap: registering all engine adapters (SPEC 35B Phase 2)")
    register_text_adapters()
    register_media_adapters()
    # SPEC 35E: external plugins installed via pip, discovered through
    # entry_points.
    register_external_engine_plugins()
    logger.info("bootstrap: engine adapter registration complete")
