#!/usr/bin/env python3
"""
================================================================================
FRAMEWORK REGISTRY  —  core/execution/frameworks/registry.py
================================================================================

PURPOSE:
    Registry for agent framework adapters. Exact FRAMEWORK_TYPE string
    match, same pattern as ScorerRegistry/ToolRegistry/engine registries.

    INV-6: duplicate FRAMEWORK_TYPE raises DuplicateFrameworkError.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 5
================================================================================
"""

import logging
from typing import Dict, Type

from core.execution.frameworks.abc import FrameworkAdapterABC

logger = logging.getLogger(__name__)


class DuplicateFrameworkError(Exception):
    """Two adapter classes claim the same FRAMEWORK_TYPE. INV-6."""


class NoFrameworkError(Exception):
    """No adapter registered for the requested FRAMEWORK_TYPE."""


class FrameworkRegistry:
    """
    Registry mapping FRAMEWORK_TYPE strings to FrameworkAdapterABC subclasses.

    One module-level instance created in bootstrap.py.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[FrameworkAdapterABC]] = {}

    def register(self, cls: Type[FrameworkAdapterABC]) -> None:
        """
        Register an adapter class by its FRAMEWORK_TYPE.

        Raises:
            DuplicateFrameworkError: if FRAMEWORK_TYPE already registered
                (INV-6).
            ValueError: if FRAMEWORK_TYPE is empty or not set.
        """
        framework_type = getattr(cls, "FRAMEWORK_TYPE", "")
        if not framework_type:
            raise ValueError(
                f"Framework adapter class {cls.__name__} has no "
                f"FRAMEWORK_TYPE set. Set FRAMEWORK_TYPE as a class "
                f"attribute before registering."
            )

        if framework_type in self._registry:
            raise DuplicateFrameworkError(
                f"FRAMEWORK_TYPE '{framework_type}' already registered by "
                f"{self._registry[framework_type].__name__}. Cannot "
                f"register {cls.__name__} — INV-6: no silent replacement."
            )

        self._registry[framework_type] = cls
        logger.debug(
            "FrameworkRegistry: registered %s (FRAMEWORK_TYPE=%s)",
            cls.__name__, framework_type,
        )

    def get(self, framework_type: str) -> FrameworkAdapterABC:
        """
        Instantiate and return an adapter for the given FRAMEWORK_TYPE.

        Raises:
            NoFrameworkError: if framework_type not in registry.
        """
        cls = self._registry.get(framework_type)
        if cls is None:
            registered = sorted(self._registry.keys())
            raise NoFrameworkError(
                f"No framework adapter registered for FRAMEWORK_TYPE="
                f"'{framework_type}'. Registered: {registered}. "
                f"Add adapter to bootstrap.py or install a plugin."
            )
        return cls()

    def get_all(self) -> Dict[str, Type[FrameworkAdapterABC]]:
        """Return all registered adapter classes keyed by FRAMEWORK_TYPE."""
        return dict(self._registry)

    def is_empty(self) -> bool:
        """Return True if no adapters have been registered yet."""
        return len(self._registry) == 0
