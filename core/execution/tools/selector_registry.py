#!/usr/bin/env python3
"""
================================================================================
TOOL SELECTOR REGISTRY  —  core/execution/tools/selector_registry.py
================================================================================
Exact SELECTOR_TYPE string match, identical pattern to ScorerRegistry/
ToolRegistry/FrameworkRegistry/OutputRegistry. INV-6: duplicate
SELECTOR_TYPE raises DuplicateToolSelectorError.

AUTHOR: Deepak Panigrahy
SPEC:   35H Part 3
================================================================================
"""

import logging
from typing import Dict, Type

from core.execution.tools.selector_abc import ToolSelectorABC

logger = logging.getLogger(__name__)


class DuplicateToolSelectorError(Exception):
    """Two selector classes claim the same SELECTOR_TYPE. INV-6."""


class NoToolSelectorError(Exception):
    """No selector registered for the requested SELECTOR_TYPE."""


class ToolSelectorRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, Type[ToolSelectorABC]] = {}

    def register(self, cls: Type[ToolSelectorABC]) -> None:
        selector_type = getattr(cls, "SELECTOR_TYPE", "")
        if not selector_type:
            raise ValueError(
                f"Selector class {cls.__name__} has no SELECTOR_TYPE set."
            )
        if selector_type in self._registry:
            raise DuplicateToolSelectorError(
                f"SELECTOR_TYPE '{selector_type}' already registered by "
                f"{self._registry[selector_type].__name__}. Cannot "
                f"register {cls.__name__} — INV-6: no silent replacement."
            )
        self._registry[selector_type] = cls
        logger.debug(
            "ToolSelectorRegistry: registered %s (SELECTOR_TYPE=%s)",
            cls.__name__, selector_type,
        )

    def get(self, selector_type: str) -> ToolSelectorABC:
        cls = self._registry.get(selector_type)
        if cls is None:
            registered = sorted(self._registry.keys())
            raise NoToolSelectorError(
                f"No tool selector registered for SELECTOR_TYPE="
                f"'{selector_type}'. Registered: {registered}."
            )
        return cls()

    def get_all(self) -> Dict[str, Type[ToolSelectorABC]]:
        return dict(self._registry)

    def is_empty(self) -> bool:
        return len(self._registry) == 0
