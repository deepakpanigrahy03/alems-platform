"""
Per turn state check for the RAF contaminating failure classes.

Spec SPEC_RAF_R1 section 4.3, plan PLAN_RAF_R1_S8 decisions D2, D3, D7.

At the end of BFCL turn t the expected state is computed by executing the
upstream ground truth calls of turn t on a copy of the agent's own state at
the start of turn t (never from the initial state: an earlier divergence
must not be blamed on the current turn). The public state hashes are then
compared. This is BFCL's own per turn state criterion, applied at the turn
boundary, which gives every recovery option the same detection point.

The check is CPU only and in memory. It runs inside the attempt window, so
its cost is timed and returned for the raf.turn_check record.
"""

import copy
import time
from typing import Any, Dict, List

from .env import BFCLEnv

SILENT_DETECTED = "silent_detected"


class DetectedFailure(Exception):
    """Raised when a turn ends in a state that differs from ground truth."""

    def __init__(self, turn: int, record: Dict[str, Any]) -> None:
        self.cls = SILENT_DETECTED
        self.turn = turn
        self.record = record
        super().__init__("state check failed at end of turn %d" % turn)


def turn_start_instances(env: BFCLEnv, start: int) -> Dict[str, Any]:
    """
    State at the start of a turn that began after `start` executed calls.

    snapshots[i] holds the state before call i + 1, so snapshots[start] is
    the state when the turn opened. If the turn has made no call yet, the
    current instances are that state.
    """
    if start < len(env.snapshots):
        return copy.deepcopy(env.snapshots[start])
    return copy.deepcopy(env.instances)


def expected_hash(env: BFCLEnv, start: int, gt_calls: List[str]) -> str:
    """Public hash after replaying gt_calls from the turn start state."""
    ref = BFCLEnv(env.entry, long_context=env.long_context)
    ref.instances = turn_start_instances(env, start)
    ref.snapshots, ref.call_log = [], []
    for call in gt_calls:
        ref.call_string(call)  # never raises; errors become results
    return ref.state_hash(public_only=True)


def check_turn(env: BFCLEnv, start: int, gt_calls: List[str]) -> Dict[str, Any]:
    """
    Compare the agent's end of turn state with the ground truth state.

    Returns:
        dict with ok, expected, actual, gt_calls (count), agent_calls
        (count in this turn) and ms (wall time of the check).
    """
    t0 = time.perf_counter_ns()
    exp = expected_hash(env, start, gt_calls)
    act = env.state_hash(public_only=True)
    return {"ok": exp == act, "expected": exp, "actual": act,
            "gt_calls": len(gt_calls), "agent_calls": env.calls_made - start,
            "ms": (time.perf_counter_ns() - t0) / 1e6}
