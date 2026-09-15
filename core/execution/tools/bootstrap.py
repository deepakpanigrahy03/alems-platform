#!/usr/bin/env python3
"""
================================================================================
TOOL BOOTSTRAP  —  core/execution/tools/bootstrap.py
================================================================================
SPEC 35G (original) + SPEC 35H corrections:
  - BuiltinToolProvider constructor takes no args (db_path moved to
    call-time ToolExecutionContext — resolves the 35G inconsistency
    where this provider alone needed a constructor arg).
  - get_tools() returns real JSON Schema parameters, not {} placeholders
    (needed by LangChain tool-calling and RetrievalToolSelector alike —
    JSON Schema is the canonical tool contract, SPEC 35H Section 2.5).

DEFERRED, NOW RESOLVED BY THIS SPEC: agentic.py's _dispatch_tool() now
consults tool_registry (see agentic.py find/replace, SPEC 35H Part 1) —
AC-3 from SPEC 35G is satisfied as of this commit.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 4, 35H Part 1 / CR-1
================================================================================
"""

import logging
from typing import Any, Dict, List

from core.execution.tools.abc import ToolDefinition, ToolExecutionContext, ToolProviderABC
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

tool_registry = ToolRegistry()

# Real JSON Schema per tool (SPEC 35H — replaces 35G's {} placeholders).
# Canonical contract consumed by both A-LEMS tool selection and any
# framework adapter's tool-calling binding (e.g. LangChain bind_tools()).
_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "calculator": {
        "type": "object",
        "properties": {
            "expression": {"type": "string",
                           "description": "Math expression to evaluate"},
        },
        "required": ["expression"],
    },
    "database_query": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "SQL SELECT query"},
        },
        "required": ["query"],
    },
    "file_processor": {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "File path under data/test_files/"},
            "operation": {"type": "string",
                          "enum": ["read", "write", "append", "list"],
                          "description": "Operation to perform"},
            "content": {"type": "string",
                        "description": "Content for write/append (omit for read/list)"},
        },
        "required": ["operation", "filename"],
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    },
    "code_executor": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"},
            "test_cases": {"type": "array", "description": "Optional test cases"},
        },
        "required": ["code"],
    },
    "api_query": {
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "API endpoint path"},
            "params": {"type": "object", "description": "Query parameters"},
        },
        "required": ["endpoint"],
    },
}


class BuiltinToolProvider(ToolProviderABC):
    """
    Wraps the six real_tools.py classes as one ToolProviderABC.
    Constructor takes no args (SPEC 35H) — db_path arrives per-call via
    ToolExecutionContext, matching every other provider's no-arg
    construction (ToolRegistry.get() calls cls() uniformly).
    """

    TOOL_PROVIDER_TYPE = "builtin"

    def execute(
        self, tool_name: str, arguments: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        tools = {
            "calculator":     CalculatorTool(),
            "database_query": DatabaseQueryTool(context.db_path),
            "file_processor": FileProcessorTool(),
            "web_search":     WebSearchTool(),
            "code_executor":  CodeExecutorTool(),
            "api_query":      APIQueryTool(),
        }
        tool = tools.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=0, error=f"Unknown builtin tool: {tool_name}",
            )
        try:
            return tool.execute(**arguments)
        except TypeError as exc:
            return ToolResult(
                success=False, result=None, tool_name=tool_name,
                duration_ns=0, error=f"Argument error: {exc}",
            )

    def get_tools(self) -> List[ToolDefinition]:
        descriptions = {
            "calculator": "Evaluate a math expression",
            "database_query": "Run a read-only SELECT query against "
                               "whitelisted tables/views",
            "file_processor": "Read/write/append/list files under "
                               "data/test_files/",
            "web_search": "Query the deterministic search stub endpoint",
            "code_executor": "Run sandboxed Python via subprocess with "
                              "blocked imports",
            "api_query": "HTTP GET to the local stub API",
        }
        return [
            ToolDefinition(name=n, description=d, parameters=_TOOL_SCHEMAS[n])
            for n, d in descriptions.items()
        ]

    def get_name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True


def _safe_register(cls, config: dict = None) -> None:
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
    if not tool_registry.is_empty():
        logger.debug("tool_bootstrap: already registered — skipping")
        return
    logger.info("tool_bootstrap: registering builtin tool provider (SPEC 35G/35H)")
    _safe_register(BuiltinToolProvider)
    register_external_tool_plugins()
    logger.info(
        "tool_bootstrap: registered %d tool provider(s): %s",
        len(tool_registry.get_all()), list(tool_registry.get_all().keys()),
    )
