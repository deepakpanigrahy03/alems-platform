#!/usr/bin/env python3
"""
================================================================================
OUTPUT REGISTRY  —  core/execution/outputs/registry.py
================================================================================

PURPOSE:
    Registry for output/export adapters. Exact OUTPUT_FORMAT string
    match, same pattern as scorers/tools/frameworks.

    INV-6: duplicate OUTPUT_FORMAT raises DuplicateOutputError.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 7
================================================================================
"""

import logging
from typing import Dict, Type

from core.execution.outputs.abc import OutputAdapterABC

logger = logging.getLogger(__name__)


class DuplicateOutputError(Exception):
    """Two adapter classes claim the same OUTPUT_FORMAT. INV-6."""


class NoOutputError(Exception):
    """No adapter registered for the requested OUTPUT_FORMAT."""


class OutputRegistry:
    """
    Registry mapping OUTPUT_FORMAT strings to OutputAdapterABC subclasses.

    One module-level instance created in bootstrap.py.
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Type[OutputAdapterABC]] = {}

    def register(self, cls: Type[OutputAdapterABC]) -> None:
        """
        Register an adapter class by its OUTPUT_FORMAT.

        Raises:
            DuplicateOutputError: if OUTPUT_FORMAT already registered
                (INV-6).
            ValueError: if OUTPUT_FORMAT is empty or not set.
        """
        output_format = getattr(cls, "OUTPUT_FORMAT", "")
        if not output_format:
            raise ValueError(
                f"Output adapter class {cls.__name__} has no OUTPUT_FORMAT "
                f"set. Set OUTPUT_FORMAT as a class attribute before "
                f"registering."
            )

        if output_format in self._registry:
            raise DuplicateOutputError(
                f"OUTPUT_FORMAT '{output_format}' already registered by "
                f"{self._registry[output_format].__name__}. Cannot "
                f"register {cls.__name__} — INV-6: no silent replacement."
            )

        self._registry[output_format] = cls
        logger.debug(
            "OutputRegistry: registered %s (OUTPUT_FORMAT=%s)",
            cls.__name__, output_format,
        )

    def get(self, output_format: str) -> OutputAdapterABC:
        """
        Instantiate and return an adapter for the given OUTPUT_FORMAT.

        Raises:
            NoOutputError: if output_format not in registry.
        """
        cls = self._registry.get(output_format)
        if cls is None:
            registered = sorted(self._registry.keys())
            raise NoOutputError(
                f"No output adapter registered for OUTPUT_FORMAT="
                f"'{output_format}'. Registered: {registered}. "
                f"Add adapter to bootstrap.py or install a plugin."
            )
        return cls()

    def get_all(self) -> Dict[str, Type[OutputAdapterABC]]:
        """Return all registered adapter classes keyed by OUTPUT_FORMAT."""
        return dict(self._registry)

    def is_empty(self) -> bool:
        """Return True if no adapters have been registered yet."""
        return len(self._registry) == 0
