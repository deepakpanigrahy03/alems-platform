"""
================================================================================
NUMERIC SCORER  —  core/execution/scorers/numeric.py
================================================================================

PURPOSE:
    Scores numeric answers with tolerance. Extracts the first number from
    the model output and compares it to the expected numeric value.
    Handles units, commas, percentages, and approximate phrasing.

    Use for: math tasks (GSM8K, arithmetic) where the answer is a number
    but the model may format it differently ("12 apples", "12.0", "twelve").

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
import re
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.context import TaskExecutionContext, ScoreResult

logger = logging.getLogger(__name__)

# Relative tolerance for float comparison.
# 1% tolerance handles floating point formatting differences.
_REL_TOLERANCE = 0.01

# Word-to-number mapping for simple cases.
_WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "hundred": 100, "thousand": 1000,
}


class NumericScorer(ScorerABC):
    """
    Numeric answer scorer with tolerance.

    Extracts numbers from both actual and expected strings,
    then compares with relative tolerance. Falls back to
    exact string match if no number can be extracted.

    Use for: GSM8K, arithmetic, calculation tasks.
    Confidence: 1.0 (deterministic extraction and comparison).
    """

    SCORER_TYPE = "numeric"
    METRIC_TYPES = ("scalar", "binary")

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score by numeric comparison with tolerance.

        Extracts first number from actual and expected.
        Returns 1.0 if within _REL_TOLERANCE, 0.0 otherwise.
        """
        try:
            actual_num = self._extract_number(actual)
            expected_num = self._extract_number(expected)

            if actual_num is None or expected_num is None:
                # Fall back to exact string match if no number found.
                actual_norm = actual.strip().lower()
                expected_norm = expected.strip().lower()
                if actual_norm == expected_norm:
                    return (1.0, 0.8, "exact string match (no number extracted)")
                return (0.0, 0.8,
                        f"no number extracted: actual='{actual[:50]}' expected='{expected[:50]}'")

            if expected_num == 0:
                match = abs(actual_num) < _REL_TOLERANCE
            else:
                rel_diff = abs(actual_num - expected_num) / abs(expected_num)
                match = rel_diff <= _REL_TOLERANCE

            if match:
                return (1.0, 1.0, f"numeric match: {actual_num} ≈ {expected_num}")
            return (0.0, 1.0,
                    f"numeric mismatch: actual={actual_num} expected={expected_num}")

        except Exception as exc:
            logger.error("NumericScorer.score failed: %s", exc)
            return (0.0, 0.0, "scorer_failed")

    def score_with_context(self, context: TaskExecutionContext) -> ScoreResult:
        """Score using TaskExecutionContext. Delegates to score()."""
        score, conf, reason = self.score(
            actual=context.model_output,
            expected=str(context.expected),
        )
        return ScoreResult(score=score, confidence=conf, reasoning=reason)

    def _extract_number(self, text: str) -> Optional[float]:
        """
        Extract the first numeric value from text.

        Handles: integers, floats, comma-formatted (1,234), percentages (77.8%),
        and simple word numbers (twelve → 12).
        """
        if not text:
            return None

        text_clean = text.strip().lower()

        # Check word numbers first.
        for word, num in _WORD_TO_NUM.items():
            if text_clean.startswith(word):
                return float(num)

        # Remove commas from formatted numbers (1,234 → 1234).
        text_clean = text_clean.replace(",", "")

        # Extract first number (integer or float, optionally followed by %).
        match = re.search(r"-?\d+\.?\d*", text_clean)
        if match:
            return float(match.group())

        return None
