"""
================================================================================
NUMERIC SCORER  —  core/execution/scorers/numeric.py
================================================================================

PURPOSE:
    Scores numeric answers. Extracts the FINAL answer number from the model
    output and compares it with the expected value.

    v3 (BUG-2, Oct 13 2026): previously extracted the FIRST number, so
    "John has 5 apples ... 5 + 7 = 12 apples" scored 5 against 12 and failed.
    Now uses answer_extract.extract_final_number():
        1. GSM8K marker "#### n"
        2. "answer is n" / "answer: n"
        3. the last number in the text
    Comparison (answer_extract.numbers_match):
        - expected with d decimals is treated as rounded: half a unit in the
          last place is accepted (0.7777 matches 0.778);
        - whole number answers stay exact (12.4 does not match 12);
        - a percent stated for a fractional answer is accepted (77.78% for
          0.778). This keeps the Sep 16 percent fix, now applied to the
          final number instead of the first.
    The old 1% relative tolerance is removed: it accepted 258 for 260.
    Word numbers ("twelve") are still accepted when the output has no digits.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.answer_extract import (
    SCORER_LOGIC_VERSION, extract_final_number, numbers_match, parse_number,
)
from core.execution.scorers.context import TaskExecutionContext, ScoreResult

logger = logging.getLogger(__name__)

# Word to number mapping, used only when the output contains no digits.
_WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "hundred": 100, "thousand": 1000,
}


class NumericScorer(ScorerABC):
    """
    Numeric final answer scorer.

    Use for: GSM8K, arithmetic, calculation tasks.
    Confidence: 1.0 when a number was extracted, 0.8 on fallback paths.
    """

    SCORER_TYPE = "numeric"
    METRIC_TYPES = ("scalar", "binary")
    LOGIC_VERSION = SCORER_LOGIC_VERSION

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """Score by final answer extraction and precision aware comparison."""
        try:
            expected_text = str(expected).strip() if expected is not None else ""
            if parse_number(expected_text) is None:
                # Expected is not a plain number: fall back to its first number.
                exp_num, _, _ = extract_final_number(expected_text.split()[0] if expected_text else "")
                if exp_num is None:
                    return (0.0, 0.8, f"expected not numeric ({self.LOGIC_VERSION}): '{expected_text[:50]}'")
                expected_text = repr(exp_num)

            value, rule, is_pct = extract_final_number(actual)
            if value is None:
                value = self._word_number(actual)
                rule, is_pct = "word_number", False
            if value is None:
                return (0.0, 0.8, f"no number extracted ({self.LOGIC_VERSION}): actual='{(actual or '')[:50]}'")

            ok, compared, how = numbers_match(value, expected_text, is_pct)
            if ok:
                return (1.0, 1.0, f"numeric match ({rule}/{how}, {self.LOGIC_VERSION}): {compared} ~ {expected_text}")
            return (0.0, 1.0, f"numeric mismatch ({rule}, {self.LOGIC_VERSION}): actual={value} expected={expected_text}")

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

    @staticmethod
    def _word_number(text: Optional[str]) -> Optional[float]:
        """Last word number in text without digits ("the answer is twelve")."""
        if not text:
            return None
        found = None
        for tok in str(text).lower().replace(".", " ").replace(",", " ").split():
            if tok in _WORD_TO_NUM:
                found = float(_WORD_TO_NUM[tok])
        return found
