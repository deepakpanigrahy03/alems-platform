#!/usr/bin/env python3
"""
================================================================================
STATIC TOOL SELECTOR  —  core/execution/tools/static_selector.py
================================================================================
Deterministic. task_context carries "allowed_tools": List[str] from
task YAML. No tools key or empty list: all tools pass through (INV-7 —
identical to pre-35H behavior, every registered tool available).

No table, no extension (unlike RetrievalToolSelector, which requires
ext-tool-selection — see CR-4). Lives entirely in core.

AUTHOR: Deepak Panigrahy
SPEC:   35H Part 3, Section 4.2
================================================================================
"""

from typing import Any, Dict, List

from core.execution.tools.abc import ToolDefinition
from core.execution.tools.selector_abc import (
    ToolSelectionContext, ToolSelectionResult, ToolSelectorABC,
)


class StaticToolSelector(ToolSelectorABC):
    SELECTOR_TYPE = "static"

    def select(
        self,
        available: List[ToolDefinition],
        task_context: Dict[str, Any],
        context: ToolSelectionContext,
    ) -> ToolSelectionResult:
        allowed = task_context.get("allowed_tools")
        if not allowed:
            # No selector-level restriction specified — every registered
            # tool passes through. Matches pre-35H all-tools behavior.
            return ToolSelectionResult(
                selected=list(available), selector_energy_uj=None,
                metadata={"filtered": False},
            )
        selected = [t for t in available if t.name in set(allowed)]
        return ToolSelectionResult(
            selected=selected, selector_energy_uj=None,
            metadata={"filtered": True, "requested": list(allowed)},
        )

    def get_name(self) -> str:
        return "static"

    def is_available(self) -> bool:
        # Pure in-memory filtering — no dependency, always available.
        return True
