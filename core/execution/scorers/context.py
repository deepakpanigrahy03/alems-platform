"""
================================================================================
SCORER CONTEXT  —  core/execution/scorers/context.py
================================================================================

PURPOSE:
    Defines TaskExecutionContext (input to all scorers) and ScoreResult
    (output from all scorers).

    TaskExecutionContext carries:
        model_output   : the LLM's final text response
        expected       : reference answer, rubric, or success conditions
        execution_trace: tool calls made, files written, API responses
                         (read by StructuralScorer, ignored by others)

    ScoreResult carries:
        score           : float [0.0, 1.0]
        confidence      : float [0.0, 1.0]
        reasoning       : human-readable explanation
        scoring_energy_uj: energy cost of this scoring call (LLM judge calls
                           only). EXCLUDED from task EpG — tracked separately
                           so quality-energy tradeoff is not contaminated by
                           the cost of measuring quality.

DESIGN NOTE:
    ScorerABC.score() signature is kept backward-compatible.
    score_with_context() is the new entry point. Default implementation
    calls score() with model_output and expected. Scorers that need the
    full context (StructuralScorer) override score_with_context() only.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    """
    Record of one tool call made during agentic execution.

    Populated from orchestration_events and ml_features in the result dict.
    Read by StructuralScorer to verify expected tool behavior.
    """

    tool_name: str
    step: int
    status: str             # "success", "failed", "timeout"
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None


@dataclass
class ExecutionTrace:
    """
    Full execution trace for one agentic attempt.

    Carries tool calls, files written, and API responses.
    Ignored by deterministic and LLM-based scorers.
    Read by StructuralScorer to check success conditions.
    """

    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    api_calls_made: int = 0
    total_steps: int = 0
    tools_used: List[str] = field(default_factory=list)

    @classmethod
    def from_agentic_result(cls, agentic_result: dict) -> "ExecutionTrace":
        """
        Build an ExecutionTrace from the agentic harness result dict.

        The result dict at save_pair time contains:
            orchestration_events: list of phase events including tool calls
            ml_features.tool_calls: count of tool calls made
            ml_features.tools_used: count (not list) of unique tools used
            task_meta.tool_graph: tool graph definition from tasks.yaml

        Step-level tool details are extracted from orchestration_events first.
        Falls back to task_meta.tool_graph when orchestration_events do not
        yield tool call records (tool_calls count from ml_features confirms
        tools ran even when event metadata lacks tool_name).

        Args:
            agentic_result: result dict from save_pair/save_single.

        Returns:
            ExecutionTrace populated from available fields.
        """
        ml = agentic_result.get("ml_features", {}) or {}
        tool_call_count = int(ml.get("tool_calls", 0) or 0)

        # Extract tool names from orchestration_events.
        orch_events = agentic_result.get("orchestration_events", []) or []
        tool_calls = []
        files_written = []
        tools_seen = []

        for i, event in enumerate(orch_events):
            metadata = event.get("metadata", {}) or {}
            tool_name = metadata.get("tool_name") or metadata.get("tool")
            if not tool_name:
                event_type = str(event.get("event_type", ""))
                if "tool" in event_type.lower():
                    tool_name = event_type.replace("tool_", "").replace("_call", "")

            if tool_name:
                status = "success"
                if metadata.get("error") or metadata.get("failed"):
                    status = "failed"

                tool_calls.append(ToolCallRecord(
                    tool_name=tool_name,
                    step=i + 1,
                    status=status,
                    arguments=metadata.get("args", {}),
                    result=metadata.get("result"),
                    error=metadata.get("error"),
                ))

                if tool_name not in tools_seen:
                    tools_seen.append(tool_name)

                if tool_name == "file_processor" and status == "success":
                    if isinstance(metadata.get("result"), dict):
                        fp = metadata["result"].get("file_path")
                        if fp:
                            files_written.append(fp)

        # Fallback: if orchestration_events gave no tool records but
        # ml_features confirms tools ran, synthesize from task_meta.tool_graph.
        # This covers cases where event metadata does not include tool_name.
        if not tool_calls and tool_call_count > 0:
            task_meta = agentic_result.get("task_meta", {}) or {}
            tool_graph = task_meta.get("tool_graph", []) or []
            for i, step in enumerate(tool_graph):
                tool_name = step.get("tool", "unknown")
                tool_calls.append(ToolCallRecord(
                    tool_name=tool_name,
                    step=i + 1,
                    status="success",  # tool_call_count > 0 means they ran
                ))
                if tool_name not in tools_seen:
                    tools_seen.append(tool_name)

        return cls(
            tool_calls=tool_calls,
            files_written=files_written,
            api_calls_made=sum(
                1 for tc in tool_calls
                if tc.tool_name in ("api_query", "web_search")
            ),
            total_steps=len(tool_calls),
            tools_used=tools_seen,
        )

    @classmethod
    def empty(cls) -> "ExecutionTrace":
        """Return an empty trace for linear (non-agentic) workflows."""
        return cls()


@dataclass
class TaskExecutionContext:
    """
    Full context passed to score_with_context() for one scoring call.

    Scorers read only what they need:
        ExactMatchScorer: model_output, expected
        NumericScorer:    model_output, expected
        SemanticScorer:   model_output, expected
        LLMJudgeScorer:   model_output, expected, rubric, judge_model
        StructuralScorer: execution_trace, expected (as conditions dict)
    """

    model_output: str
    expected: Any           # str for exact/numeric/semantic; dict for rubric/structural
    execution_trace: ExecutionTrace
    judge_model: Optional[str] = None
    rubric: Optional[dict] = None


@dataclass
class ScoreResult:
    """
    Result of one scoring call.

    scoring_energy_uj is the energy cost of this scoring call itself.
    It is stored in output_quality_judges.judge_reasoning and tracked
    in a future scoring_energy column. It is NEVER added to the task's
    EpG — the scorer's energy does not contaminate the inference measurement.
    """

    score: float                    # [0.0, 1.0]
    confidence: float               # [0.0, 1.0]
    reasoning: str                  # human-readable explanation
    scoring_energy_uj: int = 0      # energy cost of this judge call (LLM only)

    @classmethod
    def failed(cls, reason: str = "scorer_failed") -> "ScoreResult":
        """Return a failed result — used when scorer raises or API fails."""
        return cls(score=0.0, confidence=0.0, reasoning=reason, scoring_energy_uj=0)
