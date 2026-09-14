"""
================================================================================
EXACT MATCH SCORER  —  core/execution/scorers/exact_match.py
================================================================================

PURPOSE:
    Deterministic scorer. Returns 1.0 if actual matches expected after
    strip+lower normalization, 0.0 otherwise. Confidence is always 1.0.
    No API calls. No external dependencies.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC

logger = logging.getLogger(__name__)


class ExactMatchScorer(ScorerABC):
    """
    Deterministic exact-match quality scorer.

    Compares actual output to expected answer after stripping whitespace
    and lowercasing both strings. Case-insensitive, whitespace-tolerant.

    Use for: binary tasks (qa, classification, exact_match categories)
    where the correct answer is unambiguous.

    Confidence: always 1.0 — deterministic scoring has no uncertainty.
    """

    SCORER_TYPE = "exact_match"
    METRIC_TYPES = ("binary",)

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score by exact string match after normalization.

        Normalization: strip whitespace, lowercase, collapse internal whitespace.
        judge_model and rubric are ignored — this is a deterministic method.

        Args:
            actual   : LLM's output string.
            expected : Reference answer string.
            judge_model: Ignored.
            rubric   : Ignored.

        Returns:
            (1.0, 1.0, "exact match") if strings match after normalization.
            (0.0, 1.0, "no match: actual='...' expected='...'") otherwise.
        """
        try:
            # Normalize: strip, lowercase, collapse runs of whitespace.
            # This handles trailing newlines, extra spaces from generation.
            actual_norm = " ".join(actual.strip().lower().split()) if actual else ""
            expected_norm = " ".join(expected.strip().lower().split()) if expected else ""

            if actual_norm == expected_norm:
                return (1.0, 1.0, "exact match")

            # Truncate for reasoning string — keep it readable in DB.
            act_preview = actual_norm[:80] + "..." if len(actual_norm) > 80 else actual_norm
            exp_preview = expected_norm[:80] + "..." if len(expected_norm) > 80 else expected_norm
            reasoning = f"no match: actual='{act_preview}' expected='{exp_preview}'"
            return (0.0, 1.0, reasoning)

        except Exception as exc:
            logger.error("ExactMatchScorer.score failed: %s", exc)
            return (0.0, 0.0, "scorer_failed")
