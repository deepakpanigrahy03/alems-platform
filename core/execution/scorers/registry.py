"""
================================================================================
SCORER REGISTRY  —  core/execution/scorers/registry.py
================================================================================

PURPOSE:
    Registry for quality scorer adapters.
    Follows the same pattern as AdapterRegistry (35A/35B):
    one registry per scorer family, get() by SCORER_TYPE string.

    Scorers are selected by judge_method string from task_quality_config —
    no can_handle() or PRIORITY needed. Exact string match only.
    Same design as engine registry (35B).

    INV-6: duplicate SCORER_TYPE raises DuplicateRegistrationError.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Dict, Optional, Type

from core.execution.scorers.abc import ScorerABC

logger = logging.getLogger(__name__)


class DuplicateScorerError(Exception):
    """Two scorer classes claim the same SCORER_TYPE. INV-6."""


class NoScorerError(Exception):
    """No scorer registered for the requested SCORER_TYPE."""


class ScorerRegistry:
    """
    Registry mapping SCORER_TYPE strings to ScorerABC subclasses.

    One module-level instance is created in bootstrap.py and used
    by QualityJudge for all scorer lookups.

    Usage:
        registry = ScorerRegistry()
        registry.register(ExactMatchScorer)
        scorer = registry.get("exact_match")
        score, conf, reason = scorer.score(actual, expected)
    """

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._registry: Dict[str, Type[ScorerABC]] = {}

    def register(self, cls: Type[ScorerABC]) -> None:
        """
        Register a scorer class by its SCORER_TYPE.

        Args:
            cls: ScorerABC subclass with SCORER_TYPE class attribute set.

        Raises:
            DuplicateScorerError: if SCORER_TYPE already registered (INV-6).
            ValueError: if SCORER_TYPE is empty or not set.
        """
        scorer_type = getattr(cls, "SCORER_TYPE", "")
        if not scorer_type:
            raise ValueError(
                f"Scorer class {cls.__name__} has no SCORER_TYPE set. "
                f"Set SCORER_TYPE as a class attribute before registering."
            )

        if scorer_type in self._registry:
            raise DuplicateScorerError(
                f"SCORER_TYPE '{scorer_type}' already registered by "
                f"{self._registry[scorer_type].__name__}. "
                f"Cannot register {cls.__name__} — INV-6: no silent replacement."
            )

        self._registry[scorer_type] = cls
        logger.debug(
            "ScorerRegistry: registered %s (SCORER_TYPE=%s)",
            cls.__name__,
            scorer_type,
        )

    def get(self, scorer_type: str) -> ScorerABC:
        """
        Instantiate and return a scorer for the given SCORER_TYPE string.

        Args:
            scorer_type: Exact SCORER_TYPE string, e.g. "exact_match".
                         Must match what is stored in task_quality_config.judge_method.

        Returns:
            Instantiated ScorerABC subclass ready to call score().

        Raises:
            NoScorerError: if scorer_type not in registry.
        """
        cls = self._registry.get(scorer_type)
        if cls is None:
            registered = sorted(self._registry.keys())
            raise NoScorerError(
                f"No scorer registered for SCORER_TYPE='{scorer_type}'. "
                f"Registered: {registered}. "
                f"Add scorer to bootstrap.py or install a plugin."
            )

        # Instantiate fresh for each call — scorers are stateless.
        return cls()

    def get_all(self) -> Dict[str, Type[ScorerABC]]:
        """
        Return all registered scorer classes keyed by SCORER_TYPE.

        Used by diagnostic tools and the future GUI adapter panel.
        """
        return dict(self._registry)

    def is_empty(self) -> bool:
        """Return True if no scorers have been registered yet."""
        return len(self._registry) == 0
