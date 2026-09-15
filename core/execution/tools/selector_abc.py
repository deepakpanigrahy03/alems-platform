#!/usr/bin/env python3
"""
================================================================================
TOOL SELECTOR ABC  —  core/execution/tools/selector_abc.py
================================================================================
PURPOSE:
    Interface for pluggable tool selection strategies. Runs BEFORE the
    LLM sees tool definitions (SPEC 35H Section 1) — selection is a
    preparation step, not a dispatch step. Only applies to Tier-1
    (LLM-planned) tasks; Tier-2 (tool_graph) tasks specify their exact
    tools deterministically and are never filtered by a selector.

AUTHOR: Deepak Panigrahy
SPEC:   35H Part 3, CR-3, CR-5
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.execution.tools.abc import ToolDefinition


@dataclass
class ToolSelectionResult:
    selected: List[ToolDefinition]
    selector_energy_uj: Optional[int]  # None for static, real for retrieval
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSelectionContext:
    """
    CR-5: db is a live DatabaseInterface handle (typed Any to avoid a
    circular import, matching PostRunPayload.db's existing pattern in
    core/extensions/abc.py), NOT a path string. A selector that needs
    to write (e.g. RetrievalToolSelector writing to
    tool_selection_events — see CR-4) reuses the connection core
    already holds, rather than opening a second connection to the
    same SQLite file.

    energy_reader IS present here (unlike ToolExecutionContext) — this
    is the narrow, audited case where a selector legitimately measures
    its own overhead (an embedding call), the same pattern ScorerABC's
    RunContext already establishes for judge energy.
    """
    run_id: Optional[int] = None
    agent_id: Optional[str] = None
    energy_reader: Optional[Any] = None
    db: Optional[Any] = None  # DatabaseInterface


class ToolSelectorABC(ABC):
    """
    Subclasses declare SELECTOR_TYPE ("static" | "retrieval" | ...) as
    the registry key.
    """

    SELECTOR_TYPE: str = ""

    @abstractmethod
    def select(
        self,
        available: List[ToolDefinition],
        task_context: Dict[str, Any],
        context: ToolSelectionContext,
    ) -> ToolSelectionResult:
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """
        CR-3: called at experiment setup time, before any run starts.
        False means the configured selector cannot run right now
        (missing dependency, embedding model not installed, or — for
        RetrievalToolSelector specifically, per CR-4 — ext-tool-selection
        not in app_settings.yaml extensions.active, since a selector
        with no activated extension has nowhere to write its energy
        accounting row). The experiment fails immediately with a clear
        error naming the selector and reason — never a silent fallback
        to all-tools (SPEC 35H Section 4.5).
        """
        raise NotImplementedError

    def get_config_schema(self) -> Dict[str, Any]:
        return {}
