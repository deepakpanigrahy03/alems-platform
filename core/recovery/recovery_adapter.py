# core/recovery/recovery_adapter.py
#
# Recovery policy adapter family for A-LEMS.
# Chunk 35 architecture: INV-2 (adapter provides capability, never writes core tables).
# INV-3: recording to recovery_events is the RUNNER's job, not the adapter's.
# The adapter returns a decision dict. The runner records it.
#
# New adapter family: not a reader, not a serving engine, not an extension.
# Selected by YAML config name at experiment startup.
# When chunk 31 plugin packaging lands, add bootstrap registration here.

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RecoveryDecision:
    """
    Immutable result returned by RecoveryPolicyAdapter.decide().

    The runner uses this to determine where to resume execution
    and to populate the recovery_events row before restarting.
    """

    strategy: str
    # strategy_id matching recovery_taxonomy.strategy_id in DB.

    rollback_depth_turns: int
    # Turns to roll back per P6 semantics:
    #  0  = tool-only retry, LLM context preserved
    #  1  = retry current turn from LLM call
    #  N  = retry from N turns back
    # -1  = full goal restart

    rollback_target_step: int
    # orchestration_event step index where execution resumes.
    # 0 means restart from the beginning.

    recovery_point_phase: Optional[str] = None
    # Phase containing rollback_target_step: planning/execution/synthesis.
    # None when rollback_target_step = 0 (full restart has no resume phase).


class RecoveryPolicyAdapter(ABC):
    """
    Abstract base class for recovery policy adapters.

    Decides WHERE to resume execution after a failure.
    Does NOT record anything to the DB — that is the runner's job.
    Does NOT execute the recovery — that is the harness's job.

    Implementations must be stateless: decide() may be called
    concurrently for different goals and must not share mutable state.
    """

    # Stable identity string used in YAML config and registry.
    # Override in every subclass.
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
            failure_type:     failure_taxonomy.failure_type_id for this failure.
            failure_step:     orchestration_event step index where failure occurred.
            trajectory:       list of orchestration_event dicts for this goal so far.
                              Each dict has at minimum: step_index, phase, event_type.
            attempt_context:  dict with attempt_id, goal_id, attempt_number.

        Returns:
            RecoveryDecision with strategy, depth, and rollback target step.
        """
        ...

    def find_turn_start(self, trajectory: list, from_step: int) -> int:
        """
        Find the step index of the LLM call that started the turn
        containing from_step.

        A turn boundary is any step where event_type = 'llm_call'.
        Walks backward from from_step to find the nearest one.
        Returns 0 if no LLM call found (roll back to beginning).
        """
        # Walk backward from failure step to find nearest LLM call boundary.
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
        Count how many complete turns exist between turn_start_step
        and failure_step.

        Each LLM call step in that range represents one turn boundary.
        Returns 1 minimum (current turn retry).
        """
        if turn_start_step == 0 and failure_step == 0:
            return -1  # full restart

        # Count LLM call events between turn_start and failure.
        turns = sum(
            1 for step in trajectory
            if (step.get("event_type") == "llm_call"
                and turn_start_step <= step.get("step_index", 0) <= failure_step)
        )
        return max(turns, 1)

    def get_phase_for_step(self, trajectory: list, step_index: int) -> Optional[str]:
        """
        Return the phase label for a given step index.
        Returns None if step not found in trajectory.
        """
        for step in trajectory:
            if step.get("step_index") == step_index:
                return step.get("phase")
        return None


class FullRestartPolicy(RecoveryPolicyAdapter):
    """
    Default recovery policy: discard entire trajectory, restart from step 0.

    Backward compatible with all existing retry behavior.
    This is what A-LEMS has always done implicitly.
    Now it is explicit and measurable.
    """

    POLICY_ID = "full_restart"

    def decide(
        self,
        failure_type: str,
        failure_step: int,
        trajectory: list,
        attempt_context: dict,
    ) -> RecoveryDecision:
        """
        Always restart from the beginning regardless of failure type or location.
        rollback_depth_turns = -1 means full goal restart per P6.
        """
        return RecoveryDecision(
            strategy="full_restart",
            rollback_depth_turns=-1,
            rollback_target_step=0,
            recovery_point_phase=None,
        )


class LocalizedRecoveryPolicy(RecoveryPolicyAdapter):
    """
    Stephen's intervention: roll back only to the turn boundary containing
    the failure, not the entire trajectory.

    For a failure at step N inside turn T:
      - Find the LLM call step that started turn T.
      - Resume from there.
      - Preserve all context and tool results from turns before T.

    This is the policy being evaluated in Stephen's MLSys paper against
    FullRestartPolicy. The energy difference between the two is the
    paper's core measurement.
    """

    POLICY_ID = "localized"

    def decide(
        self,
        failure_type: str,
        failure_step: int,
        trajectory: list,
        attempt_context: dict,
    ) -> RecoveryDecision:
        """
        Roll back to the nearest turn boundary at or before the failure step.
        """
        if not trajectory:
            # No trajectory recorded yet — fall back to full restart.
            logger.warning(
                "LocalizedRecoveryPolicy: empty trajectory for attempt %s, "
                "falling back to full_restart",
                attempt_context.get("attempt_id"),
            )
            return RecoveryDecision(
                strategy="full_restart",
                rollback_depth_turns=-1,
                rollback_target_step=0,
                recovery_point_phase=None,
            )

        turn_start = self.find_turn_start(trajectory, failure_step)
        depth = self.compute_turn_depth(trajectory, failure_step, turn_start)
        phase = self.get_phase_for_step(trajectory, turn_start)

        return RecoveryDecision(
            strategy="turn_retry",
            rollback_depth_turns=depth,
            rollback_target_step=turn_start,
            recovery_point_phase=phase,
        )


# Registry: maps POLICY_ID string to class.
# Selected by YAML: recovery_policy.strategy: full_restart | localized
# When chunk 31 plugin packaging lands, this registry gains entry_points discovery.
_REGISTRY: dict[str, type[RecoveryPolicyAdapter]] = {
    FullRestartPolicy.POLICY_ID:    FullRestartPolicy,
    LocalizedRecoveryPolicy.POLICY_ID: LocalizedRecoveryPolicy,
}


class RecoveryPolicyRegistry:
    """
    Lightweight registry for recovery policy adapters.
    Follows chunk 35 registry contract (INV-5, INV-6).
    """

    @staticmethod
    def get(policy_id: str) -> RecoveryPolicyAdapter:
        """
        Instantiate and return the adapter for the given policy_id.

        Args:
            policy_id: POLICY_ID string from YAML config.
                       Defaults to 'full_restart' if None or empty.

        Returns:
            Instantiated RecoveryPolicyAdapter.

        Raises:
            ValueError if policy_id is unknown and not empty.
        """
        # Default to full_restart for backward compatibility (B1.6).
        resolved = policy_id or "full_restart"

        cls = _REGISTRY.get(resolved)
        if cls is None:
            raise ValueError(
                f"Unknown recovery policy '{resolved}'. "
                f"Known policies: {list(_REGISTRY.keys())}"
            )
        return cls()

    @staticmethod
    def register(cls: type[RecoveryPolicyAdapter]) -> None:
        """
        Register a custom adapter class.
        Raises ValueError on duplicate POLICY_ID (INV-6: no silent replacement).
        """
        if not cls.POLICY_ID:
            raise ValueError(f"Adapter class {cls.__name__} has no POLICY_ID set.")
        if cls.POLICY_ID in _REGISTRY:
            raise ValueError(
                f"Duplicate POLICY_ID '{cls.POLICY_ID}': "
                f"already registered as {_REGISTRY[cls.POLICY_ID].__name__}."
            )
        _REGISTRY[cls.POLICY_ID] = cls
