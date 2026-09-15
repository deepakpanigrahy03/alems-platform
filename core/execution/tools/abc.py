#!/usr/bin/env python3
"""
================================================================================
TOOL PROVIDER ABC  —  core/execution/tools/abc.py
================================================================================
SPEC 35G (original) + SPEC 35H CR-1 (ToolExecutionContext, energy_reader
removed — tools do not measure their own energy; the harness measures
tool energy by wrapping provider.execute() with energy before/after,
same as it already does via _execute_tool()'s existing instrumentation).

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 4, 35H Part 1
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.execution.tools.real_tools import ToolResult


@dataclass
class ToolDefinition:
    """Describes one callable tool exposed by a provider. parameters is
    real JSON Schema (SPEC 35H CR — no more {} placeholders)."""
    name: str
    description: str
    parameters: Dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    """
    Passed to every ToolProviderABC.execute() call.

    CR-1: NO energy_reader field. Tools never measure their own energy —
    only scorers (via RunContext) and tool selectors (via
    ToolSelectionContext) do that, for the narrow, audited case of
    measuring their own overhead. Tool execution energy is measured by
    the harness wrapping the execute() call itself, exactly as
    agentic.py's existing _execute_tool() already does with its own
    before/after timing.
    """
    db_path: str
    run_id: Optional[int] = None
    agent_id: Optional[str] = None


class ToolProviderABC(ABC):
    """Base class for all tool provider adapters. TOOL_PROVIDER_TYPE is
    the registry key. Constructor takes no required args (SPEC 35H —
    db_path moved from constructor to call-time ToolExecutionContext,
    resolving the 35G inconsistency where BuiltinToolProvider required
    a constructor arg unlike every other family)."""

    TOOL_PROVIDER_TYPE: str = ""

    @abstractmethod
    def execute(
        self, tool_name: str, arguments: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Never raises — returns ToolResult(success=False) on any failure."""
        raise NotImplementedError

    @abstractmethod
    def get_tools(self) -> List[ToolDefinition]:
        """No context needed — may be dynamic (e.g. an MCP-client provider
        querying its server), but does not depend on run/agent identity."""
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    def get_config_schema(self) -> Dict[str, Any]:
        return {}
