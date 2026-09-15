#!/usr/bin/env python3
"""
================================================================================
FRAMEWORK BOOTSTRAP  —  core/execution/frameworks/bootstrap.py
================================================================================

PURPOSE:
    Register the builtin framework adapter and discover external
    framework plugins via entry_points(group="alems.frameworks").

KNOWN LIMITATION (documented, not silent — DC-3):
    metadata carries the complete, unmodified result dict from
    execute_comparison() — output/success/total_duration_ms/steps
    are real field mappings (confirmed against agentic.py's execute()
    return shape), not a substitute for it. Anything not surfaced by
    the four mapped fields is still available via metadata directly.

    harness.py is NOT changed by this file. AgenticExecutor's actual
    construction site was not found in harness.py or
    experiment_runner.py (grep confirmed) — it is built elsewhere in
    the tree. The call-site edit is deferred until that construction
    site is located, not guessed.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 5
================================================================================
"""

import logging
from typing import Any, Dict, List

from core.execution.agentic import EXECUTION_STATUS_SUCCESS
from core.execution.frameworks.abc import FrameworkAdapterABC, FrameworkResult
from core.execution.frameworks.registry import FrameworkRegistry, DuplicateFrameworkError
from core.execution.tools.abc import ToolProviderABC
from core.plugin_discovery import discover_plugins
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

# Module-level singleton — mirrors scorer_registry / tool_registry pattern.
framework_registry = FrameworkRegistry()


class BuiltinFrameworkAdapter(FrameworkAdapterABC):
    """
    Wraps the existing AgenticExecutor as FRAMEWORK_TYPE = "builtin".

    Does not modify AgenticExecutor or its call site in any way —
    this is purely an additive wrapper so the builtin runtime is
    selectable through the same registry as any external framework
    plugin, satisfying AC-9 (builtin works with zero plugins installed).
    """

    FRAMEWORK_TYPE = "builtin"

    def execute_task(
        self,
        task_config: Dict[str, Any],
        tools: List[ToolProviderABC],
        engine: Any,
    ) -> FrameworkResult:
        """
        task_config expected keys:
            model_config: dict passed straight to AgenticExecutor(...)
            task: str, the task prompt
            tool_graph: list, optional

        `tools` and `engine` are accepted per the ABC contract but not
        yet consumed here — AgenticExecutor resolves its own engine
        internally via ModelFactory using model_config, and builds its
        own tool_map internally rather than taking one externally.
        Both are real gaps, tracked, not silent.
        """
        # Local import — agentic.py has heavy dependencies (psutil,
        # requests, ModelFactory chain); avoid pulling them into every
        # process that imports this bootstrap module, same isolation
        # principle as reader/engine bootstrap.
        from core.execution.agentic import AgenticExecutor

        model_config = task_config.get("model_config", {})
        task = task_config.get("task", "")
        tool_graph = task_config.get("tool_graph")

        executor = AgenticExecutor(model_config)
        exec_result = executor.execute_comparison(task, tool_graph=tool_graph)

        # Real field mapping, confirmed against agentic.py's execute() return
        # dict (SPEC 35G — no more placeholders):
        #   response          -> output
        #   execution.status  -> success (success|failure|partial_failure)
        #   phase_times.total_ms -> total_duration_ms
        #   events            -> steps (the actual per-LLM-call/tool-call log)
        execution_meta = exec_result.get("execution", {})
        return FrameworkResult(
            output=exec_result.get("response", ""),
            success=execution_meta.get("status") == EXECUTION_STATUS_SUCCESS,
            total_duration_ms=exec_result.get("phase_times", {}).get("total_ms", 0.0),
            steps=exec_result.get("events", []),
            metadata=exec_result,
        )

    def get_name(self) -> str:
        return "builtin"

    def get_framework_type(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True


def _safe_register(cls, config: dict = None) -> None:
    """Same pattern as scorer/tool bootstrap _safe_register."""
    try:
        framework_registry.register(cls)
    except DuplicateFrameworkError:
        raise
    except Exception as exc:
        logger.warning(
            "framework_bootstrap: failed to register %s: %s — skipping",
            getattr(cls, "__name__", repr(cls)), exc,
        )


def register_external_framework_plugins() -> None:
    """Discover external framework plugins via entry_points(group="alems.frameworks")."""
    names = discover_plugins(
        group="alems.frameworks",
        register_fn=lambda cls, cfg: _safe_register(cls, cfg),
        core_version=_CORE_VERSION,
    )
    if names:
        logger.info(
            "framework_bootstrap: %d external framework(s) registered via "
            "entry_points: %s", len(names), names,
        )


def register_all_frameworks() -> None:
    """Register builtin, then discover external plugins. Idempotent."""
    if not framework_registry.is_empty():
        logger.debug("framework_bootstrap: already registered — skipping")
        return
    logger.info("framework_bootstrap: registering builtin framework (SPEC 35G)")
    _safe_register(BuiltinFrameworkAdapter)

    # SPEC 35H Part 2: LangChain, built on the native-client architecture
    # (see CHUNK_31_SUPPLEMENTARY_RESEARCH_v2.md) — LangChain uses its own
    # ChatOllama client, not a TextGenABC bridge. Only registered if the
    # optional langchain/langchain-ollama packages are actually installed;
    # absence is not an error, same as any other optional plugin.
    try:
        from core.execution.frameworks.langchain_adapter import LangChainFrameworkAdapter
        if LangChainFrameworkAdapter().is_available():
            _safe_register(LangChainFrameworkAdapter)
        else:
            logger.info(
                "framework_bootstrap: LangChainFrameworkAdapter not available "
                "(langchain/langchain-ollama not installed) — skipping"
            )
    except ImportError as exc:
        logger.debug(
            "framework_bootstrap: LangChainFrameworkAdapter not importable: %s", exc
        )

    register_external_framework_plugins()
    logger.info(
        "framework_bootstrap: registered %d framework(s): %s",
        len(framework_registry.get_all()), list(framework_registry.get_all().keys()),
    )
