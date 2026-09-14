"""
================================================================================
STRUCTURAL SCORER  —  core/execution/scorers/structural.py
================================================================================

PURPOSE:
    Scores agentic tasks by verifying execution trace conditions rather than
    comparing text output. The model's final text is irrelevant — what matters
    is whether the right tools were called with the right results.

    Conditions are defined in the task's expectation block:

        expectation:
          scorer_type: structural
          expected_source: inline
          conditions:
            - tool_called: write_file
            - file_exists: output.txt
            - api_called: true
            - tool_succeeded: database_query

    Score = fraction of conditions that passed.
    All conditions pass → 1.0. None pass → 0.0.
    Confidence: 1.0 (deterministic — reads execution trace directly).

    This scorer OVERRIDES score_with_context() and reads execution_trace.
    It does NOT implement score(actual, expected) meaningfully — text output
    is not relevant to structural correctness.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Dict, List, Optional, Tuple

from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.context import TaskExecutionContext, ScoreResult

logger = logging.getLogger(__name__)


class StructuralScorer(ScorerABC):
    """
    Structural correctness scorer for agentic tasks.

    Reads the execution trace to verify conditions rather than comparing
    text output. Used for tool-using tasks where the expected behavior is
    defined by what actions were taken, not what text was produced.

    Supported condition keys:
        tool_called: <name>      — at least one call to this tool was made
        tool_succeeded: <name>   — at least one call to this tool succeeded
        api_called: true         — at least one api_query tool call was made
        file_exists: <path>      — a file at this path was written
        min_tool_calls: <n>      — at least N tool calls were made total
        all_steps_succeeded: true — all steps in the trace succeeded
    """

    SCORER_TYPE = "structural"
    METRIC_TYPES = ("binary", "scalar")

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Not meaningful for structural scoring — use score_with_context().

        Returns a conservative failure to signal misconfiguration.
        Structural scoring requires an execution trace, not text strings.
        """
        logger.warning(
            "StructuralScorer.score() called without execution trace. "
            "Use score_with_context() for structural scoring."
        )
        return (0.0, 0.0,
                "structural_scorer_requires_context: use score_with_context()")

    def score_with_context(self, context: TaskExecutionContext) -> ScoreResult:
        """
        Score by verifying execution trace conditions.

        Args:
            context: TaskExecutionContext with execution_trace populated
                     and expected containing the conditions dict or list.

        Returns:
            ScoreResult with score = fraction of conditions passed.
        """
        try:
            conditions = self._parse_conditions(context.expected)
            if not conditions:
                return ScoreResult(
                    score=0.0, confidence=0.5,
                    reasoning="no conditions defined in expectation block"
                )

            trace = context.execution_trace
            results = []

            for condition in conditions:
                passed, explanation = self._check_condition(condition, trace)
                results.append((passed, explanation))

            passed_count = sum(1 for p, _ in results if p)
            total = len(results)
            score = passed_count / total if total > 0 else 0.0

            reasoning_parts = [
                f"{'✓' if p else '✗'} {exp}" for p, exp in results
            ]
            reasoning = f"{passed_count}/{total} conditions passed: " + "; ".join(reasoning_parts)

            return ScoreResult(
                score=score,
                confidence=1.0,
                reasoning=reasoning,
            )

        except Exception as exc:
            logger.error("StructuralScorer.score_with_context failed: %s", exc)
            return ScoreResult.failed("structural_scorer_failed")

    def _parse_conditions(self, expected) -> List[Dict]:
        """
        Parse expected into a list of condition dicts.

        Accepts: list of dicts, or a single dict.
        Example: [{"tool_called": "write_file"}, {"file_exists": "output.txt"}]
        """
        if isinstance(expected, list):
            return expected
        if isinstance(expected, dict):
            # Single condition dict — wrap in list.
            return [expected]
        return []

    def _check_condition(self, condition: Dict, trace) -> Tuple[bool, str]:
        """
        Check one condition against the execution trace.

        Returns (passed, explanation) tuple.
        """
        for key, value in condition.items():

            if key == "tool_called":
                tool_name = str(value)
                called = any(tc.tool_name == tool_name for tc in trace.tool_calls)
                return (called, f"tool_called={tool_name}: {'yes' if called else 'no'}")

            if key == "tool_succeeded":
                tool_name = str(value)
                succeeded = any(
                    tc.tool_name == tool_name and tc.status == "success"
                    for tc in trace.tool_calls
                )
                return (succeeded,
                        f"tool_succeeded={tool_name}: {'yes' if succeeded else 'no'}")

            if key == "api_called":
                called = trace.api_calls_made > 0
                return (called, f"api_called: {trace.api_calls_made} calls")

            if key == "file_exists":
                filename = str(value)
                exists = any(
                    filename in f for f in trace.files_written
                )
                return (exists,
                        f"file_exists={filename}: {'found' if exists else 'not found'}")

            if key == "min_tool_calls":
                min_n = int(value)
                actual_n = len(trace.tool_calls)
                passed = actual_n >= min_n
                return (passed, f"min_tool_calls={min_n}: actual={actual_n}")

            if key == "all_steps_succeeded":
                if not trace.tool_calls:
                    return (False, "all_steps_succeeded: no tool calls recorded")
                all_ok = all(tc.status == "success" for tc in trace.tool_calls)
                failed = [tc.tool_name for tc in trace.tool_calls if tc.status != "success"]
                return (all_ok,
                        f"all_steps_succeeded: {'yes' if all_ok else f'failed={failed}'}")

            # Unknown condition key — skip with warning.
            logger.warning("StructuralScorer: unknown condition key '%s'", key)
            return (False, f"unknown_condition={key}")

        return (False, "empty_condition")
