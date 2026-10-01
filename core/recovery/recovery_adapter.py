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


# Contracts live in the SDK since 39.5.1a.2; implementations below subclass them.
from alems_sdk.policies import RecoveryDecision, RecoveryPolicyAdapter  # noqa: E402


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
    The localized recovery intervention: roll back only to the turn boundary containing
    the failure, not the entire trajectory.

    For a failure at step N inside turn T:
      - Find the LLM call step that started turn T.
      - Resume from there.
      - Preserve all context and tool results from turns before T.

    This is the policy being evaluated for the recovery depth study. against
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
