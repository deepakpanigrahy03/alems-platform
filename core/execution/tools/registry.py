#!/usr/bin/env python3
"""
================================================================================
TOOL PROVIDER REGISTRY  —  core/execution/tools/registry.py
================================================================================

PURPOSE:
    Registry for tool provider adapters. Identical shape to
    core/execution/scorers/registry.py — one registry, exact
    TOOL_PROVIDER_TYPE string match, no can_handle()/PRIORITY.

    INV-6: duplicate TOOL_PROVIDER_TYPE raises DuplicateToolProviderError.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 4
================================================================================
"""

import logging
from typing import Dict, Type

from core.execution.tools.abc import ToolProviderABC

logger = logging.getLogger(__name__)


class DuplicateToolProviderError(Exception):
    """Two provider classes claim the same TOOL_PROVIDER_TYPE. INV-6."""


class NoToolProviderError(Exception):
    """No provider registered for the requested TOOL_PROVIDER_TYPE."""


class ToolRegistry:
    """
    Registry mapping TOOL_PROVIDER_TYPE strings to ToolProviderABC subclasses.

    One module-level instance created in bootstrap.py.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[ToolProviderABC]] = {}

    def register(self, cls: Type[ToolProviderABC]) -> None:
        """
        Register a provider class by its TOOL_PROVIDER_TYPE.

        Raises:
            DuplicateToolProviderError: if TOOL_PROVIDER_TYPE already
                registered (INV-6).
            ValueError: if TOOL_PROVIDER_TYPE is empty or not set.
        """
        provider_type = getattr(cls, "TOOL_PROVIDER_TYPE", "")
        if not provider_type:
            raise ValueError(
                f"Tool provider class {cls.__name__} has no TOOL_PROVIDER_TYPE "
                f"set. Set TOOL_PROVIDER_TYPE as a class attribute before "
                f"registering."
            )

        if provider_type in self._registry:
            raise DuplicateToolProviderError(
                f"TOOL_PROVIDER_TYPE '{provider_type}' already registered by "
                f"{self._registry[provider_type].__name__}. Cannot register "
                f"{cls.__name__} — INV-6: no silent replacement."
            )

        self._registry[provider_type] = cls
        logger.debug(
            "ToolRegistry: registered %s (TOOL_PROVIDER_TYPE=%s)",
            cls.__name__, provider_type,
        )

    def get(self, provider_type: str) -> ToolProviderABC:
        """
        Instantiate and return a provider for the given TOOL_PROVIDER_TYPE.

        Raises:
            NoToolProviderError: if provider_type not in registry.
        """
        cls = self._registry.get(provider_type)
        if cls is None:
            registered = sorted(self._registry.keys())
            raise NoToolProviderError(
                f"No tool provider registered for TOOL_PROVIDER_TYPE="
                f"'{provider_type}'. Registered: {registered}. "
                f"Add provider to bootstrap.py or install a plugin."
            )
        return cls()

    def get_all(self) -> Dict[str, Type[ToolProviderABC]]:
        """Return all registered provider classes keyed by TOOL_PROVIDER_TYPE."""
        return dict(self._registry)

    def is_empty(self) -> bool:
        """Return True if no providers have been registered yet."""
        return len(self._registry) == 0
