"""
UNIT TEST SCORER (STUB)  —  core/execution/scorers/unit_test.py
================================================================================

PURPOSE:
    Placeholder for deterministic pass/fail scoring against a test harness
    (distinct from LLM-judged scoring). Registered so judge_method='unit_test'
    resolves to a real scorer object instead of raising NoScorerError, but
    STATUS="stub" tells OutputQualityExtension to route it to
    score_method='stub_skipped' rather than treating a stub call as a
    genuine score.

    Required for Paper B (qEpG) credibility per SPEC 35J Problem 3.
    Full sandboxed implementation is a separate chunk, out of scope here.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC


class UnitTestScorer(ScorerABC):
    """
    Stub for unit-test-based scoring. Not implemented.

    Use for: tasks with a deterministic pass/fail test harness, once built.
    Currently: always returns a clearly-labeled failure tuple so callers
    can distinguish "not implemented" from "scored zero."
    """

    SCORER_TYPE = "unit_test"
    METRIC_TYPES = ("testsuite",)
    STATUS = "stub"

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        return (0.0, 0.0, "unit_test scorer not yet implemented")
