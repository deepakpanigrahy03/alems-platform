#!/usr/bin/env python3
"""
================================================================================
TOOL BOOTSTRAP  —  core/execution/tools/bootstrap.py
================================================================================

PURPOSE:
    Register the builtin tool provider and discover external tool
    provider plugins via entry_points(group="alems.tools").

    BuiltinToolProvider wraps the six existing real_tools.py classes
    unchanged — same instantiation, same execute() signatures. This
    file does not alter tool behavior in any way, only exposes the
    existing tools through the ToolProviderABC/ToolRegistry contract.

    DEFERRED (not in this file): agentic.py's _dispatch_tool() still
    builds its own local tool_map and does not consult tool_registry.
    Wiring that requires reviewing AgenticExecutor's full tool dispatch
    path first — a measurement-adjacent change, done separately once
    that review is complete. Until then AC-9 holds (builtin runtime
    works with zero plugins) but AC-3 (external tool replaces builtin
    at the agentic-runtime level) is not yet satisfied — the registry
    exists and plugins can register into it, but nothing consumes it
    from the execution path yet.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 4
================================================================================
"""

import logging
from typing import Any, Dict, List

from core.execution.tools.abc import ToolDefinition, ToolProviderABC
from core.execution.tools.registry import ToolRegistry, DuplicateToolProviderError
from core.execution.tools.real_tools import (
    CalculatorTool,
    DatabaseQueryTool,
    FileProcessorTool,
    WebSearchTool,
    CodeExecutorTool,
    APIQueryTool,
    ToolResult,
)
from core.plugin_discovery import discover_plugins
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

# Module-level singleton — mirrors scorer_registry / text_registry pattern.
tool_registry = ToolRegistry()


class BuiltinToolProvider(ToolProviderABC):
    """
    Wraps the six real_tools.py classes as one ToolProviderABC.

    db_path default matches DatabaseQueryTool's own documented default
    ("data/experiments.db") — unchanged from real_tools.py.
    """

    TOOL_PROVIDER_TYPE = "builtin"

    def __init__(self, db_path: str = "data/experiments.db"):
        # Same six tools, same construction as agentic.py's tool_map today.
        self._tools = {
            "calculator":     CalculatorTool(),
            "database_query": DatabaseQueryTool(db_path),
            "file_processor": FileProcessorTool(),
            "web_search":     WebSearchTool(),
            "code_executor":  CodeExecutorTool(),
            "api_query":      APIQueryTool(),
        }

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Dispatch to the underlying tool's execute(). Never raises."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=0, error=f"Unknown builtin tool: {tool_name}",
            )
        try:
            return tool.execute(**arguments)
        except TypeError as exc:
            # Argument mismatch — caller passed keys the tool's execute()
            # doesn't accept. Return as ToolResult, never propagate.
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=0, error=f"Argument error: {exc}",
            )

    def get_tools(self) -> List[ToolDefinition]:
        """
        Static list — descriptions are illustrative, not authoritative
        JSON schemas. Framework adapters that need real schemas should
        derive them from each tool's execute() signature directly.
        """
        return [
            ToolDefinition("calculator", "Evaluate a math expression", {}),
            ToolDefinition("database_query", "Run a read-only SELECT query "
                            "against whitelisted tables/views", {}),
            ToolDefinition("file_processor", "Read/write/append/list files "
                            "under data/test_files/", {}),
            ToolDefinition("web_search", "Query the deterministic search "
                            "stub endpoint", {}),
            ToolDefinition("code_executor", "Run sandboxed Python via "
                            "subprocess with blocked imports", {}),
            ToolDefinition("api_query", "HTTP GET to the local stub API", {}),
        ]

    def get_name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True


def _safe_register(cls, config: dict = None) -> None:
    """
    Register cls, re-raising DuplicateToolProviderError (programming
    error) and logging all other exceptions as warnings. Same shape
    as scorer/reader/engine bootstrap _safe_register.
    """
    try:
        tool_registry.register(cls)
    except DuplicateToolProviderError:
        raise
    except Exception as exc:
        logger.warning(
            "tool_bootstrap: failed to register %s: %s — skipping",
            getattr(cls, "__name__", repr(cls)), exc,
        )


def register_external_tool_plugins() -> None:
    """
    Discover and register externally pip-installed tool providers via
    entry_points(group="alems.tools"). Additive to the builtin provider.
    """
    names = discover_plugins(
        group="alems.tools",
        register_fn=lambda cls, cfg: _safe_register(cls, cfg),
        core_version=_CORE_VERSION,
    )
    if names:
        logger.info(
            "tool_bootstrap: %d external tool provider(s) registered via "
            "entry_points: %s", len(names), names,
        )


def register_all_tool_providers() -> None:
    """
    Register the builtin provider, then discover external plugins.
    Idempotent — skips if already registered.
    """
    if not tool_registry.is_empty():
        logger.debug("tool_bootstrap: already registered — skipping")
        return
    logger.info("tool_bootstrap: registering builtin tool provider (SPEC 35G)")
    _safe_register(BuiltinToolProvider)
    register_external_tool_plugins()
    logger.info(
        "tool_bootstrap: registered %d tool provider(s): %s",
        len(tool_registry.get_all()), list(tool_registry.get_all().keys()),
    )
