#!/usr/bin/env python3
"""
================================================================================
TOOL PROVIDER ABC  —  core/execution/tools/abc.py
================================================================================

PURPOSE:
    Interface for pluggable agentic tool providers.
    Follows the same pattern as ScorerABC (SPEC 35G Section 3):
    one registry keyed by TOOL_PROVIDER_TYPE, exact string selection,
    no can_handle()/PRIORITY needed.

    One provider can expose many tools. get_tools() returns a list —
    this is what lets a single registered provider (e.g. an MCP-client
    adapter) expose an unbounded external tool catalog without any
    core code change. Tool selection/routing across a large catalog
    is out of scope here — deferred to SPEC 35H.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 4
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

# Reuse the existing ToolResult dataclass — real_tools.py already defines
# the single return type every tool uses. No duplicate type here.
from core.execution.tools.real_tools import ToolResult


@dataclass
class ToolDefinition:
    """
    Describes one callable tool exposed by a provider.

    parameters follows JSON schema shape so it can be handed directly
    to a framework's function-calling interface without translation.
    """
    name: str
    description: str
    parameters: Dict[str, Any]


class ToolProviderABC(ABC):
    """
    Base class for all tool provider adapters.

    Subclasses declare TOOL_PROVIDER_TYPE as a class attribute
    (e.g. "builtin", "serpapi", "mcp_client") and are registered
    into ToolRegistry keyed by that string.
    """

    # Set by every concrete subclass — used as the registry key.
    TOOL_PROVIDER_TYPE: str = ""

    @abstractmethod
    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """
        Execute one named tool with the given arguments.

        Never raises — mirrors real_tools.py convention. Returns
        ToolResult(success=False, ...) on any failure, including an
        unknown tool_name.
        """
        raise NotImplementedError

    @abstractmethod
    def get_tools(self) -> List[ToolDefinition]:
        """
        Return every tool this provider currently exposes.

        May be dynamic — an MCP-client provider queries its connected
        server at call time rather than returning a fixed list.
        """
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """Human-readable provider name for logs and diagnostics."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True if this provider can be used right now.

        False for a provider whose backend (e.g. a remote MCP server)
        is unreachable — never raises to signal unavailability.
        """
        raise NotImplementedError

    def get_config_schema(self) -> Dict[str, Any]:
        """
        Declare the plugin config schema for app_settings.yaml
        plugins.<name> validation. Empty dict means no config required —
        the default for providers with no constructor arguments.
        """
        return {}
