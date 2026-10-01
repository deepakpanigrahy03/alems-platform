"""
alems_sdk.policies: retry and recovery policy contracts (D3.3 harness.retry,
harness.recovery).

Physical home since 39.5.1a.2; core.retry.retry_adapter and
core.recovery.recovery_adapter re-export these objects. Implementations
(FlatRetryAdapter, EARAdapter, FullRestartPolicy, LocalizedRecoveryPolicy,
registries) stay in core. Zero imports from core or scripts (INV-14).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from alems_sdk.config_schema import Configurable

class RetryPolicyAdapter(Configurable, ABC):
    """
    Base class for all retry policy engines.

    should_retry() is the single decision point called after every failed
    attempt. It runs on the hot path between attempts, so it must be fast; any
    logging write must be deferred.
    """

    @abstractmethod
    def should_retry(
        self,
        failure_type: str,
        attempt_number: int,
        goal_id: int,
        run_context: dict,
    ) -> dict:
        """
        Decide whether to retry after a failure.

        Args:
            failure_type:   canonical failure type from the failure classifier.
            attempt_number: current attempt number (1 indexed).
            goal_id:        goal_execution.goal_id for this goal.
            run_context:    dict with keys conn, policy, run_id, attempt_id,
                            budget_remaining_uj (optional), ear_policy_id (EAR).

        Returns:
            dict with 'action' ('retry' | 'abort' | 'fallback') and 'reason'.
        """
        ...


@dataclass
class RecoveryDecision:
    """
    Immutable result returned by RecoveryPolicyAdapter.decide().

    The runner uses it to choose where to resume and to populate the
    recovery_events row before restarting.
    """

    # strategy_id matching recovery_taxonomy.strategy_id.
    strategy: str

    # Turns to roll back: 0 tool only retry (LLM context kept), 1 retry the
    # current turn from its LLM call, N retry from N turns back, -1 full restart.
    rollback_depth_turns: int

    # orchestration_event step index where execution resumes; 0 = beginning.
    rollback_target_step: int

    # Phase containing rollback_target_step (planning, execution, synthesis);
    # None for a full restart, which has no resume phase.
    recovery_point_phase: Optional[str] = None


class RecoveryPolicyAdapter(Configurable, ABC):
    """
    Decides where to resume execution after a failure.

    Records nothing (the runner does) and executes nothing (the harness does).
    Implementations are stateless: decide() may run concurrently for goals.
    """

    # Stable identity used in YAML config and the registry; override.
    POLICY_ID: str = ""

    @abstractmethod
    def decide(
        self,
        failure_type: str,
        failure_step: int,
        trajectory: list,
        attempt_context: dict,
    ) -> RecoveryDecision:
        """
        Decide where to resume execution after a failure.

        Args:
            failure_type:    failure_taxonomy.failure_type_id.
            failure_step:    step index where the failure occurred.
            trajectory:      orchestration_event dicts so far (step_index,
                             phase, event_type at minimum).
            attempt_context: attempt_id, goal_id, attempt_number.
        """
        ...

    def find_turn_start(self, trajectory: list, from_step: int) -> int:
        """
        Step index of the LLM call that started the turn containing from_step.

        A turn boundary is any step with event_type 'llm_call'; 0 if none.
        """
        # Walk backward to the nearest LLM call boundary at or before from_step.
        for step in reversed(trajectory):
            if step.get("step_index", 0) <= from_step:
                if step.get("event_type") == "llm_call":
                    return step["step_index"]
        return 0

    def compute_turn_depth(
        self,
        trajectory: list,
        failure_step: int,
        turn_start_step: int,
    ) -> int:
        """
        Number of turns between turn_start_step and failure_step (minimum 1).

        Returns -1 (full restart) when both indices are 0.
        """
        if turn_start_step == 0 and failure_step == 0:
            return -1  # full restart

        # Each LLM call in the range is one turn boundary.
        turns = sum(
            1 for step in trajectory
            if (step.get("event_type") == "llm_call"
                and turn_start_step <= step.get("step_index", 0) <= failure_step)
        )
        return max(turns, 1)

    def get_phase_for_step(self, trajectory: list, step_index: int) -> Optional[str]:
        """Phase label for step_index, or None if the step is not found."""
        for step in trajectory:
            if step.get("step_index") == step_index:
                return step.get("phase")
        return None


__all__ = ["RetryPolicyAdapter", "RecoveryDecision", "RecoveryPolicyAdapter"]
