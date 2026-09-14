"""
================================================================================
TEST SUITE — 8.5C.1 Expectation System and New Scorers
================================================================================

Tests for Expectation schema, ExpectedSourceResolver, TaskExpectationAdapter,
NumericScorer, and StructuralScorer.

Run with:
    python3 -m pytest tests/test_expectation_system.py -v

AUTHOR: Deepak Panigrahy
================================================================================
"""

import pytest
from core.execution.expectation.schema import Expectation, VALID_SCORER_TYPES
from core.execution.expectation.resolver import ExpectedSourceResolver
from core.execution.scorers.numeric import NumericScorer
from core.execution.scorers.structural import StructuralScorer
from core.execution.scorers.context import (
    TaskExecutionContext, ExecutionTrace, ToolCallRecord, ScoreResult
)


# ---------------------------------------------------------------------------
# Expectation schema tests
# ---------------------------------------------------------------------------

class TestExpectationSchema:

    def test_parse_exact_expectation(self):
        data = {"scorer_type": "exact", "expected_source": "inline", "answer": "12"}
        exp = Expectation.from_dict(data)
        assert exp.scorer_type == "exact"
        assert exp.answer == "12"
        assert exp.is_scoreable() is True

    def test_parse_numeric_expectation(self):
        data = {"scorer_type": "numeric", "expected_source": "inline", "answer": "12"}
        exp = Expectation.from_dict(data)
        assert exp.scorer_type == "numeric"
        assert exp.get_expected_value() == "12"

    def test_parse_structural_expectation(self):
        data = {
            "scorer_type": "structural",
            "expected_source": "inline",
            "conditions": [{"tool_called": "write_file"}],
        }
        exp = Expectation.from_dict(data)
        assert exp.scorer_type == "structural"
        assert exp.requires_execution_trace() is True
        assert exp.conditions == [{"tool_called": "write_file"}]

    def test_parse_rubric_expectation(self):
        data = {
            "scorer_type": "rubric",
            "expected_source": "inline",
            "rubric": {"key_concepts": ["energy"], "min_words": 100},
        }
        exp = Expectation.from_dict(data)
        assert exp.scorer_type == "rubric"
        assert exp.get_expected_value() == {"key_concepts": ["energy"], "min_words": 100}

    def test_none_scorer_type_not_scoreable(self):
        exp = Expectation.from_dict({"scorer_type": "none"})
        assert exp.is_scoreable() is False

    def test_empty_dict_gives_none(self):
        exp = Expectation.from_dict({})
        assert exp.scorer_type == "none"
        assert exp.is_scoreable() is False

    def test_invalid_scorer_type_raises(self):
        with pytest.raises(ValueError):
            Expectation.from_dict({"scorer_type": "invalid_type"})

    def test_requires_execution_trace_only_for_structural(self):
        for stype in ["exact", "numeric", "semantic", "rubric", "none"]:
            exp = Expectation.from_dict({"scorer_type": stype})
            assert exp.requires_execution_trace() is False
        exp = Expectation.from_dict({
            "scorer_type": "structural",
            "conditions": [{"tool_called": "x"}],
        })
        assert exp.requires_execution_trace() is True


# ---------------------------------------------------------------------------
# ExpectedSourceResolver tests
# ---------------------------------------------------------------------------

class TestExpectedSourceResolver:

    def test_inline_returns_answer(self):
        resolver = ExpectedSourceResolver()
        exp = Expectation.from_dict({
            "scorer_type": "exact", "expected_source": "inline", "answer": "42"
        })
        assert resolver.resolve(exp) == "42"

    def test_inline_returns_conditions_for_structural(self):
        resolver = ExpectedSourceResolver()
        conditions = [{"tool_called": "write_file"}]
        exp = Expectation.from_dict({
            "scorer_type": "structural",
            "expected_source": "inline",
            "conditions": conditions,
        })
        assert resolver.resolve(exp) == conditions

    def test_none_scorer_returns_none(self):
        resolver = ExpectedSourceResolver()
        exp = Expectation.none()
        assert resolver.resolve(exp) is None

    def test_benchmark_dataset_raises_not_implemented(self):
        resolver = ExpectedSourceResolver()
        exp = Expectation.from_dict({
            "scorer_type": "exact",
            "expected_source": "benchmark_dataset",
            "answer": "12",
        })
        with pytest.raises(NotImplementedError):
            resolver.resolve(exp)

    def test_computed_at_runtime_raises_not_implemented(self):
        resolver = ExpectedSourceResolver()
        exp = Expectation.from_dict({
            "scorer_type": "exact",
            "expected_source": "computed_at_runtime",
            "answer": "12",
        })
        with pytest.raises(NotImplementedError):
            resolver.resolve(exp)


# ---------------------------------------------------------------------------
# NumericScorer tests
# ---------------------------------------------------------------------------

class TestNumericScorer:

    def test_exact_integer_match(self):
        scorer = NumericScorer()
        score, conf, reason = scorer.score("12", "12")
        assert score == 1.0
        assert conf == 1.0

    def test_float_within_tolerance(self):
        scorer = NumericScorer()
        score, _, _ = scorer.score("12.0", "12")
        assert score == 1.0

    def test_comma_formatted_number(self):
        scorer = NumericScorer()
        score, _, _ = scorer.score("1,234", "1234")
        assert score == 1.0

    def test_number_in_sentence(self):
        scorer = NumericScorer()
        # LLM often responds "John has 12 apples"
        score, _, _ = scorer.score("John has 12 apples.", "12")
        assert score == 1.0

    def test_wrong_number_fails(self):
        scorer = NumericScorer()
        score, conf, _ = scorer.score("11", "12")
        assert score == 0.0
        assert conf == 1.0

    def test_word_number(self):
        scorer = NumericScorer()
        score, _, _ = scorer.score("twelve", "12")
        assert score == 1.0

    def test_scorer_type(self):
        assert NumericScorer.SCORER_TYPE == "numeric"

    def test_percentage(self):
        scorer = NumericScorer()
        score, _, _ = scorer.score("77.8%", "77.8")
        assert score == 1.0

    def test_score_with_context(self):
        scorer = NumericScorer()
        context = TaskExecutionContext(
            model_output="The answer is 12.",
            expected="12",
            execution_trace=ExecutionTrace.empty(),
        )
        result = scorer.score_with_context(context)
        assert result.score == 1.0
        assert isinstance(result, ScoreResult)


# ---------------------------------------------------------------------------
# StructuralScorer tests
# ---------------------------------------------------------------------------

def _make_trace(tool_calls=None, files_written=None, api_calls=0) -> ExecutionTrace:
    calls = tool_calls or []
    return ExecutionTrace(
        tool_calls=calls,
        files_written=files_written or [],
        api_calls_made=api_calls,
        total_steps=len(calls),
        tools_used=[tc.tool_name for tc in calls],
    )


class TestStructuralScorer:

    def test_tool_called_condition_passes(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("write_file", 1, "success"),
        ])
        context = TaskExecutionContext(
            model_output="done",
            expected=[{"tool_called": "write_file"}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 1.0

    def test_tool_called_condition_fails(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[])
        context = TaskExecutionContext(
            model_output="done",
            expected=[{"tool_called": "write_file"}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 0.0

    def test_tool_succeeded_condition(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("database_query", 1, "success"),
        ])
        context = TaskExecutionContext(
            model_output="",
            expected=[{"tool_succeeded": "database_query"}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 1.0

    def test_tool_succeeded_fails_when_tool_failed(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("database_query", 1, "failed"),
        ])
        context = TaskExecutionContext(
            model_output="",
            expected=[{"tool_succeeded": "database_query"}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 0.0

    def test_partial_conditions_give_partial_score(self):
        scorer = StructuralScorer()
        trace = _make_trace(
            tool_calls=[ToolCallRecord("write_file", 1, "success")],
            files_written=[],  # file write recorded but no path tracked
        )
        context = TaskExecutionContext(
            model_output="",
            expected=[
                {"tool_called": "write_file"},   # passes
                {"file_exists": "output.txt"},    # fails
            ],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 0.5

    def test_min_tool_calls_condition(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("database_query", 1, "success"),
            ToolCallRecord("file_processor", 2, "success"),
        ])
        context = TaskExecutionContext(
            model_output="",
            expected=[{"min_tool_calls": 2}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 1.0

    def test_all_steps_succeeded(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("database_query", 1, "success"),
            ToolCallRecord("file_processor", 2, "success"),
        ])
        context = TaskExecutionContext(
            model_output="",
            expected=[{"all_steps_succeeded": True}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 1.0

    def test_all_steps_succeeded_fails_on_partial(self):
        scorer = StructuralScorer()
        trace = _make_trace(tool_calls=[
            ToolCallRecord("database_query", 1, "success"),
            ToolCallRecord("file_processor", 2, "failed"),
        ])
        context = TaskExecutionContext(
            model_output="",
            expected=[{"all_steps_succeeded": True}],
            execution_trace=trace,
        )
        result = scorer.score_with_context(context)
        assert result.score == 0.0

    def test_scorer_type(self):
        assert StructuralScorer.SCORER_TYPE == "structural"

    def test_empty_conditions_gives_zero(self):
        scorer = StructuralScorer()
        context = TaskExecutionContext(
            model_output="",
            expected=[],
            execution_trace=ExecutionTrace.empty(),
        )
        result = scorer.score_with_context(context)
        assert result.score == 0.0

    def test_score_without_context_warns(self):
        scorer = StructuralScorer()
        score, conf, reason = scorer.score("actual", "expected")
        assert score == 0.0
        assert "requires_context" in reason


# ---------------------------------------------------------------------------
# ExecutionTrace.from_agentic_result tests
# ---------------------------------------------------------------------------

class TestExecutionTrace:

    def test_from_empty_agentic_result(self):
        trace = ExecutionTrace.from_agentic_result({})
        assert len(trace.tool_calls) == 0
        assert trace.api_calls_made == 0

    def test_from_agentic_result_with_steps(self):
        # New implementation reads from ml_features.tool_calls count
        # and falls back to task_meta.tool_graph when orchestration_events
        # do not yield tool names.
        result = {
            "ml_features": {"tool_calls": 2, "tools_used": 2},
            "orchestration_events": [],
            "task_meta": {
                "tool_graph": [
                    {"step": 1, "tool": "database_query", "depends_on": []},
                    {"step": 2, "tool": "file_processor", "depends_on": []},
                ]
            }
        }
        trace = ExecutionTrace.from_agentic_result(result)
        assert len(trace.tool_calls) == 2
        assert trace.tool_calls[0].tool_name == "database_query"
        assert trace.tool_calls[0].status == "success"
        assert trace.tool_calls[1].tool_name == "file_processor"

    def test_empty_trace(self):
        trace = ExecutionTrace.empty()
        assert len(trace.tool_calls) == 0
        assert len(trace.files_written) == 0
