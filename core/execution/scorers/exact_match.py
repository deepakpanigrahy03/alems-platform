"""
================================================================================
EXACT MATCH SCORER  —  core/execution/scorers/exact_match.py
================================================================================

PURPOSE:
    Deterministic answer scorer. Returns 1.0 if the expected answer appears
    in the output as a whole token sequence after normalization, 0.0
    otherwise. Confidence is always 1.0. No API calls.

    v3 (BUG-1, Oct 13 2026): previously compared the WHOLE output to the
    expected answer, so "The first president was George Washington." failed
    against "George Washington" (0% on 359 back scored rows). Now uses
    answer_extract.contains_answer(): NFKC, lowercase, punctuation removed,
    leading article dropped, whole token containment. Substrings do not
    match ("120" does not contain "12"; "cattle" does not contain "cat").
    If the rubric supplies "answer_set" (closed choices), naming two or more
    options counts as a hedge and fails.

    SCORER_TYPE stays "exact_match" so task_quality_config rows keep working.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.answer_extract import SCORER_LOGIC_VERSION, contains_answer, normalize

logger = logging.getLogger(__name__)


class ExactMatchScorer(ScorerABC):
    """
    Deterministic answer match scorer (whole token containment).

    Use for: short fixed answers (names, labels, symbols, single words).
    Confidence: always 1.0.
    """

    SCORER_TYPE = "exact_match"
    METRIC_TYPES = ("binary",)
    LOGIC_VERSION = SCORER_LOGIC_VERSION

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score by whole token containment after normalization.

        Args:
            actual     : LLM output string.
            expected   : Reference answer string.
            judge_model: Ignored.
            rubric     : Optional dict; "answer_set" (list of strings) enables
                         the hedge guard for closed answer sets.

        Returns:
            (1.0, 1.0, reasoning) on match, (0.0, 1.0, reasoning) otherwise.
        """
        try:
            answer_set = None
            if isinstance(rubric, dict):
                answer_set = rubric.get("answer_set") or rubric.get("choices")

            exp_norm = normalize(expected)
            if not exp_norm and not normalize(actual):
                return (1.0, 1.0, f"exact match (both empty, {self.LOGIC_VERSION})")

            passed, rule = contains_answer(actual or "", expected or "", answer_set)
            if passed:
                return (1.0, 1.0, f"exact match ({rule}, {self.LOGIC_VERSION}): '{exp_norm[:80]}'")

            act_norm = normalize(actual)
            act_preview = act_norm[:80] + "..." if len(act_norm) > 80 else act_norm
            reasoning = (f"no match ({rule}, {self.LOGIC_VERSION}): "
                         f"actual='{act_preview}' expected='{exp_norm[:80]}'")
            return (0.0, 1.0, reasoning)

        except Exception as exc:
            logger.error("ExactMatchScorer.score failed: %s", exc)
            return (0.0, 0.0, "scorer_failed")
