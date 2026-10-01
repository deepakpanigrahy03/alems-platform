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

from core.execution.scorers.registry import ScorerRegistry
from core.registry.loader import load_group
from alems import __version__ as _CORE_VERSION

logger = logging.getLogger(__name__)

# Module-level singleton — one registry for the process lifetime.
# Imported by quality_judge.py for all scorer lookups.
scorer_registry = ScorerRegistry()


def register_all_scorers() -> None:
    """
    Register every quality scorer through the single plugin path.
    Idempotent: skips if already registered.
    A broken scorer is refused by the loader and never blocks the others.
    """
    if not scorer_registry.is_empty():
        logger.debug("scorer_bootstrap: already registered — skipping")
        return

    logger.info("scorer_bootstrap: registering built-in scorers")

    # Every origin: runtime builtins, pip installed scorers, sandbox local
    # scorers (design 7.8). Failures are recorded as refusals by the loader
    # (WP 1a.3 section 5); this bootstrap never interprets them (G67).
    load_group(
        "alems.scorers",
        lambda cls, cfg: scorer_registry.register(cls),
        _CORE_VERSION,
    )

    logger.info(
        "scorer_bootstrap: registered %d scorers: %s",
        len(scorer_registry.get_all()),
        list(scorer_registry.get_all().keys()),
    )
