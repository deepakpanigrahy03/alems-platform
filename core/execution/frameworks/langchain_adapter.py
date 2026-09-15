#!/usr/bin/env python3
"""
================================================================================
LANGCHAIN FRAMEWORK ADAPTER  —  core/execution/frameworks/langchain_adapter.py
================================================================================
SPEC 35H Part 2, per CHUNK_31_SUPPLEMENTARY_RESEARCH_v2.md's corrected
architecture (native client, not a TextGenABC bridge).

Constructs LangChain's own native ChatOllama client, pointed at the
exact provider endpoint A-LEMS's own model_config already resolved —
NOT routed through TextGenABC. Full native tool-calling from day one.
fidelity_tier is reported as "native" (see the supplementary research
doc, Section 2, for what a non-native tier would mean and when it
could occur).

VERIFIED AGAINST INSTALLED PACKAGES (2026-09-15, gn100), not assumed
from documentation:
  langchain==0.3.30, langchain-core==0.3.86, langchain-ollama==0.3.10
  - create_tool_calling_agent: real signature confirmed via inspect.
  - ChatOllama: pydantic model, fields confirmed via model_fields —
    model (required), base_url (optional), temperature (optional).
  - bind_tools() confirmed present on ChatOllama.
Re-verify all of the above if this file is touched after a LangChain
version bump — per this project's own established discipline, do not
trust these findings to still hold at a later version.

AUTHOR: Deepak Panigrahy
SPEC:   35H Part 2
================================================================================
"""

import logging
import time
from typing import Any, Dict, List

from core.execution.frameworks.abc import FrameworkAdapterABC, FrameworkResult
from core.execution.tools.abc import ToolExecutionContext, ToolProviderABC

logger = logging.getLogger(__name__)


class _StepCaptureCallback:
    """
    Real LangChain callback handler — captures actual on_llm_start/
    on_llm_end/on_tool_start/on_tool_end events with wall-clock
    timestamps, for FrameworkResult.steps (SPEC 35H IC-2: sorted by
    start_time_ns before being returned).

    Subclasses BaseCallbackHandler at construction time (import kept
    local to avoid a hard langchain dependency anywhere this module
    is merely imported for type-checking, matching every other
    bootstrap's import-isolation convention in this codebase).
    """

    def __init__(self):
        from langchain_core.callbacks import BaseCallbackHandler

        class _Handler(BaseCallbackHandler):
            def __init__(inner_self):
                inner_self.events: List[Dict[str, Any]] = []

            def on_llm_start(inner_self, serialized, prompts, **kwargs):
                inner_self.events.append({
                    "type": "llm_call", "start_time_ns": time.time_ns(),
                    "end_time_ns": None,
                })

            def on_llm_end(inner_self, response, **kwargs):
                if inner_self.events:
                    for ev in reversed(inner_self.events):
                        if ev["type"] == "llm_call" and ev["end_time_ns"] is None:
                            ev["end_time_ns"] = time.time_ns()
                            break

            def on_tool_start(inner_self, serialized, input_str, **kwargs):
                inner_self.events.append({
                    "type": "tool_call",
                    "tool_name": serialized.get("name", "unknown"),
                    "start_time_ns": time.time_ns(),
                    "end_time_ns": None,
                })

            def on_tool_end(inner_self, output, **kwargs):
                if inner_self.events:
                    for ev in reversed(inner_self.events):
                        if ev["type"] == "tool_call" and ev["end_time_ns"] is None:
                            ev["end_time_ns"] = time.time_ns()
                            break

        self._handler = _Handler()

    @property
    def handler(self):
        return self._handler

    def get_sorted_steps(self) -> List[Dict[str, Any]]:
        return sorted(self._handler.events, key=lambda e: e["start_time_ns"])


def _tool_provider_to_langchain_tools(
    tools: List[ToolProviderABC], context: ToolExecutionContext,
):
    """
    Converts every ToolDefinition from every provider into a real
    LangChain StructuredTool. One closure per tool, capturing the
    owning provider and the shared ToolExecutionContext (CR-1: no
    energy_reader on this context — tools never measure their own
    energy, the harness measures around the whole execute_task() call).
    """
    from langchain_core.tools import StructuredTool

    lc_tools = []
    for provider in tools:
        for tool_def in provider.get_tools():
            def _make_func(p=provider, name=tool_def.name):
                def _func(**kwargs):
                    result = p.execute(name, kwargs, context)
                    if not result.success:
                        return f"ERROR: {result.error}"
                    return str(result.result)
                return _func

            lc_tools.append(StructuredTool.from_function(
                func=_make_func(),
                name=tool_def.name,
                description=tool_def.description,
                # args_schema handling: see CHUNK_31 research note open
                # question — verified against installed StructuredTool
                # signature before this line was finalized, not assumed.
                args_schema=tool_def.parameters,
            ))
    return lc_tools


class LangChainFrameworkAdapter(FrameworkAdapterABC):
    FRAMEWORK_TYPE = "langchain"

    def execute_task(
        self,
        task_config: Dict[str, Any],
        tools: List[ToolProviderABC],
        engine: Any,
    ) -> FrameworkResult:
        """
        task_config expected keys:
            model_config: dict — same shape AgenticExecutor's __init__
                expects (model_id, api_endpoint, temperature, ...).
                Required so this adapter hits the EXACT same model
                server as BuiltinFrameworkAdapter would for the same
                task — non-negotiable for any energy/quality
                comparison between them (CHUNK_31 research, Section 0).
            task: str — the task prompt.
        """
        from langchain.agents import AgentExecutor, create_tool_calling_agent
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_ollama import ChatOllama

        model_config = task_config.get("model_config", {})
        task = task_config.get("task", "")

        model = ChatOllama(
            model=model_config.get("model_id", "unknown"),
            base_url=model_config.get("api_endpoint") or None,
            temperature=model_config.get("temperature", 0.7),
        )

        context = ToolExecutionContext(
            db_path=model_config.get("db_path", "data/experiments.db"),
        )
        lc_tools = _tool_provider_to_langchain_tools(tools, context)

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful assistant."),
            ("placeholder", "{chat_history}"),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])

        agent = create_tool_calling_agent(model, lc_tools, prompt)
        executor = AgentExecutor(agent=agent, tools=lc_tools)

        step_capture = _StepCaptureCallback()
        start_ns = time.time_ns()
        try:
            result = executor.invoke(
                {"input": task}, config={"callbacks": [step_capture.handler]},
            )
            output = result.get("output", "")
            success = True
        except Exception as exc:
            logger.warning("LangChainFrameworkAdapter: task failed: %s", exc)
            output = f"Error: {exc}"
            success = False
        total_duration_ms = (time.time_ns() - start_ns) / 1e6

        return FrameworkResult(
            output=output,
            success=success,
            total_duration_ms=total_duration_ms,
            steps=step_capture.get_sorted_steps(),
            metadata={"fidelity_tier": "native", "framework": "langchain"},
        )

    def get_name(self) -> str:
        return "langchain"

    def get_framework_type(self) -> str:
        return "langchain"

    def is_available(self) -> bool:
        try:
            import langchain  # noqa: F401
            import langchain_ollama  # noqa: F401
            return True
        except ImportError:
            return False
