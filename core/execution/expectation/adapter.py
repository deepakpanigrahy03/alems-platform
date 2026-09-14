"""
================================================================================
TASK EXPECTATION ADAPTER  —  core/execution/expectation/adapter.py
================================================================================

PURPOSE:
    Bridges the expectation block (what correctness means for a task) with
    the scorer registry (how to evaluate it). Two lookups, cleanly separated:

        1. ExpectedSourceResolver.resolve(expectation) → expected value
        2. ScorerRegistry.get(scorer_type) → scorer instance
        3. scorer.score_with_context(context) → ScoreResult

    This is the single entry point for quality scoring. experiment_runner.py
    calls this; it never touches individual scorers directly.

    Future extension:
        Adding a new scorer = register in bootstrap.py.
        Adding a new data source = add to resolver.py.
        Neither requires changes to this adapter.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional

from core.execution.expectation.schema import Expectation
from core.execution.expectation.resolver import ExpectedSourceResolver
from core.execution.scorers.bootstrap import scorer_registry, register_all_scorers
from core.execution.scorers.context import TaskExecutionContext, ExecutionTrace, ScoreResult

logger = logging.getLogger(__name__)

# Register scorers at import time — idempotent.
register_all_scorers()

_resolver = ExpectedSourceResolver()


class TaskExpectationAdapter:
    """
    Orchestrates expectation resolution and scorer dispatch.

    Instantiate once per session and reuse across attempts.
    Thread-safe — no mutable state after construction.

    Usage:
        adapter = TaskExpectationAdapter()
        expectation = Expectation.from_dict(task_meta.get("expectation", {}))
        result = adapter.score(
            expectation=expectation,
            model_output=actual_output,
            agentic_result=agentic_result,
        )
        # result.score, result.confidence, result.reasoning
    """

    def score(
        self,
        expectation: Expectation,
        model_output: str,
        agentic_result: Optional[dict] = None,
        judge_model: Optional[str] = None,
    ) -> ScoreResult:
        """
        Score one attempt using the task's expectation block.

        Steps:
        1. Resolve expected value from expectation source.
        2. Build execution trace from agentic_result (if structural scoring).
        3. Get scorer from registry by scorer_type.
        4. Call score_with_context() on the scorer.
        5. Return ScoreResult.

        Args:
            expectation   : Parsed Expectation from the task definition.
            model_output  : The LLM's actual text response.
            agentic_result: Full agentic result dict (for execution trace).
                            None for linear workflow scoring.
            judge_model   : Judge model override for LLM-based scorers.

        Returns:
            ScoreResult with score, confidence, reasoning, scoring_energy_uj.
            ScoreResult.failed() when scoring cannot proceed.
        """
        # Skip tasks with no expectation.
        if not expectation.is_scoreable():
            logger.debug(
                "TaskExpectationAdapter: scorer_type=none or no expected value — skipping"
            )
            return ScoreResult(
                score=0.0, confidence=0.0,
                reasoning="no_expectation_defined",
            )

        # Resolve expected value from source.
        try:
            expected_value = _resolver.resolve(expectation)
        except NotImplementedError as exc:
            logger.warning("TaskExpectationAdapter: source not implemented: %s", exc)
            return ScoreResult.failed(f"source_not_implemented: {exc}")
        except Exception as exc:
            logger.error("TaskExpectationAdapter: resolver failed: %s", exc)
            return ScoreResult.failed("resolver_failed")

        if expected_value is None:
            return ScoreResult.failed("expected_value_is_none")

        # Build execution trace for structural scoring.
        if expectation.requires_execution_trace():
            if agentic_result:
                trace = ExecutionTrace.from_agentic_result(agentic_result)
            else:
                trace = ExecutionTrace.empty()
                logger.warning(
                    "TaskExpectationAdapter: structural scoring requested "
                    "but agentic_result is None — trace will be empty"
                )
        else:
            trace = ExecutionTrace.empty()

        # Build context.
        context = TaskExecutionContext(
            model_output=model_output or "",
            expected=expected_value,
            execution_trace=trace,
            judge_model=judge_model,
            rubric=expectation.rubric,
        )

        # Get scorer from registry.
        scorer_type = expectation.scorer_type
        try:
            scorer = scorer_registry.get(scorer_type)
        except Exception as exc:
            logger.error(
                "TaskExpectationAdapter: no scorer for type='%s': %s",
                scorer_type, exc,
            )
            return ScoreResult.failed(f"no_scorer_for_{scorer_type}")

        # Call score_with_context() — use override if scorer has it,
        # otherwise fall back to score(actual, expected).
        try:
            if hasattr(scorer, "score_with_context") and callable(scorer.score_with_context):
                result = scorer.score_with_context(context)
            else:
                # Backward compat: call score(actual, expected) directly.
                score, conf, reason = scorer.score(
                    actual=context.model_output,
                    expected=str(context.expected),
                    judge_model=context.judge_model,
                    rubric=context.rubric,
                )
                result = ScoreResult(score=score, confidence=conf, reasoning=reason)

            logger.debug(
                "TaskExpectationAdapter: scorer_type=%s score=%.3f confidence=%.3f",
                scorer_type, result.score, result.confidence,
            )
            return result

        except Exception as exc:
            logger.error(
                "TaskExpectationAdapter: scorer '%s' raised: %s",
                scorer_type, exc,
            )
            return ScoreResult.failed(f"scorer_raised: {exc}")
