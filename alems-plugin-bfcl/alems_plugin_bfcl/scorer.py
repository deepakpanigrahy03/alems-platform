"""
BFCL state scorer (SCORER_TYPE "bfcl_state").

Implements the upstream BFCL multi turn checker (eval_checker/
multi_turn_eval/multi_turn_checker.py at the vendored commit) on replay:

    actual   : JSON {"task_id": id, "turns": [[call string, ...], ...]}
               as produced by BFCLToolProvider.scorer_output()
    expected : the BFCL task id (or a JSON object with key "task_id")

For every turn, the model's calls and the ground truth calls are executed
on two separate environments that persist across turns, as upstream does.
After every turn with a non empty ground truth:
    1. empty model turn          -> fail (empty_turn_model_response)
    2. public state of every instance equal  (state_checker)
    3. every ground truth result of this turn appears among all model
       results so far, unordered with multiplicity  (response_checker)
Turns with an empty ground truth are executed but not judged, exactly as
in the upstream main checker (6 such turns exist, in entries 167 and 180
of both categories). Method invoke order is not checked; upstream has
that check disabled as well.

Deterministic: score 1.0 or 0.0 with confidence 1.0. Malformed input or
any internal error returns (0.0, 0.0, "scorer_failed: ...") and never
raises (ScorerABC contract). The scorer never writes anywhere.
"""

import json
from typing import List, Optional, Tuple

from alems_sdk.scoring import ScorerABC

from .env import BFCLEnv
from .tasks import ground_truth, load_entry


def _compare_instances(model_obj, truth_obj) -> dict:
    """Upstream _compare_instances: public attributes of the truth object."""
    diffs = {}
    for name in vars(truth_obj):
        if name.startswith("_"):
            continue
        if getattr(model_obj, name) != getattr(truth_obj, name):
            diffs[name] = True
    return diffs


def _is_subset_unordered(needed: List[str], have: List[str]) -> List[str]:
    """Items of needed not covered by have, counting multiplicity."""
    pool = list(have)
    missing = []
    for item in needed:
        if item in pool:
            pool.remove(item)
        else:
            missing.append(item)
    return missing


def _parse_actual(actual: str) -> Tuple[str, List[List[str]]]:
    """Decode the scorer input; raises ValueError on a malformed payload."""
    data = json.loads(actual)
    turns = data.get("turns")
    if not isinstance(turns, list) or not all(isinstance(t, list) for t in turns):
        raise ValueError("turns must be a list of lists of call strings")
    return str(data.get("task_id", "")), turns


def _parse_expected(expected: str) -> str:
    """Task id from the expected field (plain id or JSON with task_id)."""
    text = (expected or "").strip()
    if text.startswith("{"):
        return str(json.loads(text)["task_id"])
    return text


def _check_turn(model_env, truth_env, model_calls, truth_calls, history) -> str:
    """
    Run one turn on both environments and check it.

    Returns:
        "" when the turn passes, else the upstream error type.
    """
    history.extend(model_env.call_string(c) for c in model_calls)
    truth_results = [truth_env.call_string(c) for c in truth_calls]
    if not truth_calls:
        # Upstream main checker: such a turn is executed (so later state
        # checks see its effects) but not judged.
        return ""
    if not model_calls:
        return "multi_turn:empty_turn_model_response"
    for name, truth_obj in truth_env.instances.items():
        if _compare_instances(model_env.instances[name], truth_obj):
            return "multi_turn:instance_state_mismatch:" + name
    if _is_subset_unordered(truth_results, history):
        return "multi_turn:execution_response_mismatch"
    return ""


def check(task_id: str, model_turns: List[List[str]]) -> Tuple[bool, str]:
    """
    Upstream multi turn verdict for one task.

    Returns:
        (valid, reason) where reason names the first failing turn.
    """
    entry = load_entry(task_id)
    truth = ground_truth(task_id)
    if len(model_turns) < len(truth):
        # Turns the agent never reached count as empty model turns.
        model_turns = model_turns + [[] for _ in range(len(truth) - len(model_turns))]
    model_env, truth_env, history = BFCLEnv(entry), BFCLEnv(entry), []
    for i, truth_calls in enumerate(truth):
        error = _check_turn(model_env, truth_env, model_turns[i], truth_calls, history)
        if error:
            return False, "turn %d: %s" % (i, error)
    return True, "all %d turns match ground truth" % len(truth)


class BFCLStateScorer(ScorerABC):
    """Deterministic BFCL multi turn scorer; see module docstring."""

    SCORER_TYPE = "bfcl_state"
    METRIC_TYPES = ("binary",)

    def score(self, actual: str, expected: str,
              judge_model: Optional[str] = None,
              rubric: Optional[dict] = None) -> Tuple[float, float, str]:
        """Return (1.0 or 0.0, 1.0, reason); scorer_failed on bad input."""
        try:
            actual_id, turns = _parse_actual(actual)
            task_id = _parse_expected(expected)
            if actual_id and actual_id != task_id:
                return 0.0, 0.0, "scorer_failed: task id mismatch %s vs %s" % (actual_id, task_id)
            valid, reason = check(task_id, turns)
            return (1.0 if valid else 0.0), 1.0, reason
        except Exception as exc:  # contract: never raise
            return 0.0, 0.0, "scorer_failed: %s: %s" % (type(exc).__name__, exc)

    def get_name(self) -> str:
        return "bfcl_state"
