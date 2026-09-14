"""
================================================================================
SCORER BOOTSTRAP  —  core/execution/scorers/bootstrap.py
================================================================================

PURPOSE:
    Register all built-in quality scorers into the scorer registry.
    Called once at QualityJudge import time.

    Follows the same pattern as core/readers/bootstrap.py (35A) and
    core/execution/adapters/bootstrap.py (35B).

    Phase 1 (this chunk): built-in scorers registered here.
    Phase 35G: entry_points(group="alems.scorers") discovery added.
               This file becomes optional as community scorers register
               via pip install.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging

from core.execution.scorers.registry import ScorerRegistry, DuplicateScorerError

logger = logging.getLogger(__name__)

# Module-level singleton — one registry for the process lifetime.
# Imported by quality_judge.py for all scorer lookups.
scorer_registry = ScorerRegistry()


def register_all_scorers() -> None:
    """
    Register all built-in quality scorer adapters.

    Idempotent — skips if already registered.
    Import errors are caught per scorer so one broken scorer
    does not prevent others from loading.
    """
    if not scorer_registry.is_empty():
        logger.debug("scorer_bootstrap: already registered — skipping")
        return

    logger.info("scorer_bootstrap: registering built-in scorers")

    try:
        from core.execution.scorers.exact_match import ExactMatchScorer
        scorer_registry.register(ExactMatchScorer)
    except Exception as exc:
        logger.error("scorer_bootstrap: failed to register ExactMatchScorer: %s", exc)

    try:
        from core.execution.scorers.semantic import SemanticScorer
        scorer_registry.register(SemanticScorer)
    except Exception as exc:
        logger.error("scorer_bootstrap: failed to register SemanticScorer: %s", exc)

    try:
        from core.execution.scorers.llm_judge import LLMJudgeScorer
        scorer_registry.register(LLMJudgeScorer)
    except Exception as exc:
        logger.error("scorer_bootstrap: failed to register LLMJudgeScorer: %s", exc)

    logger.info(
        "scorer_bootstrap: registered %d scorers: %s",
        len(scorer_registry.get_all()),
        list(scorer_registry.get_all().keys()),
    )
