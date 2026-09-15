#!/usr/bin/env python3
"""
================================================================================
FRAMEWORK ADAPTER ABC  —  core/execution/frameworks/abc.py
================================================================================

PURPOSE:
    Interface for pluggable agent framework adapters (LangChain, CrewAI,
    AutoGen, A2A, and the existing builtin agentic runtime).

    Selection is by exact FRAMEWORK_TYPE string match from experiment
    config, same as engines (registry.get(key)) — no can_handle()/
    PRIORITY needed.

    NOT INCLUDED HERE: the builtin adapter that wraps AgenticExecutor,
    and the harness.py call-site change to use this registry. Both
    require reviewing AgenticExecutor's constructor and harness.py's
    executor construction path first — deferred pending that review,
    tracked as the next step, not silently dropped.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 5
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.execution.tools.abc import ToolProviderABC


@dataclass
class FrameworkResult:
    """
    Result of one framework's execute_task() call.

    metadata is the escape hatch for framework-specific fields the
    generic schema doesn't cover — e.g. the builtin adapter can set
    metadata to the exact dict its wrapped AgenticExecutor already
    returns, so nothing downstream of harness.py needs to change
    shape just because the framework became pluggable.
    """
    output: str
    success: bool
    total_duration_ms: float
    steps: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class FrameworkAdapterABC(ABC):
    """
    Base class for all agent framework adapters.

    Subclasses declare FRAMEWORK_TYPE as a class attribute
    (e.g. "builtin", "langchain", "crewai", "a2a") and are registered
    into FrameworkRegistry keyed by that string.
    """

    FRAMEWORK_TYPE: str = ""

    @abstractmethod
    def execute_task(
        self,
        task_config: Dict[str, Any],
        tools: List[ToolProviderABC],
        engine: Any,
    ) -> FrameworkResult:
        """
        Execute one task through this framework.

        The measurement harness wraps this entire call — energy is
        read before and after, never inside. This adapter must not
        perform its own energy measurement (INV-1, INV-2).

        Args:
            task_config: task definition dict (prompt, tool_graph, etc.)
            tools: tool providers available to this task, resolved
                from ToolRegistry by the caller before this call.
            engine: TextGenABC/MediaABC instance already resolved by
                ModelFactory — this adapter does not resolve its own
                engine.

        Returns:
            FrameworkResult with per-step breakdown for energy
            attribution.
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_framework_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    def get_config_schema(self) -> Dict[str, Any]:
        return {}
