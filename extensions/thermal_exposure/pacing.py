"""
extensions/thermal_exposure/pacing.py: session plans for thermal sessions.

Registered in alems.harness.pacing. Each plan only decides the next step;
run_experiment executes it (safety guard, retries, persistence, recording).

  back_to_back        goals one after another, no planned pause
  fixed_pause         planned pause = gap_factor x tau_s after each goal
  matched_throughput  planned pause = 3600 * p / S - t (one workflow per session),
                      refused before the session when below zero
  linear_first        per goal: linear; the agent only if linear failed, right after
  calibration_steps   hold a load task for hold_s, then rest for rest_s; repeat

Goal order: blocks of every (task, workflow) pair, one block per repeat,
shuffled with seed + repeat, so a stored seed reproduces the order.
No planned pause follows the last goal (the recording's cooling tail covers it).
tau_s always carries tau_status and tau_source; a provisional value is never
presented as measured.
"""

import random
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.session_plan import SessionPlanABC, done, goal, load_step, wait

TAU_STATUS = ("PROVISIONAL", "PILOT_ONLY", "MEASURED")
_GOALS = {
    "goals": {"type": "list", "required": True},
    "repeats": {"type": "number", "required": True},
    "seed": {"type": "number", "required": True},
}


def _list_errors(cls, params, keys):
    # type: (Any, Dict[str, Any], Tuple[str, ...]) -> List[str]
    """List parameters must be non-empty lists of strings (the SDK checks numbers and choices)."""
    out = []
    for k in keys:
        v = params.get(k)
        if v is not None and (not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v)):
            out.append("%s: %s must be a non-empty list of names" % (cls.NAME, k))
    return out


def goal_order(goals, workflows, repeats, seed):
    # type: (List[str], List[str], int, int) -> List[Tuple[str, str]]
    """Every (task, workflow) pair once per repeat; each block shuffled with seed + repeat."""
    order = []
    for r in range(int(repeats)):
        block = [(t, w) for t in goals for w in workflows]
        random.Random(int(seed) + r).shuffle(block)
        order.extend(block)
    return order


class _OrderedGoals(SessionPlanABC):
    """Shared base: a fixed goal order with an optional planned pause between goals."""

    PARAMS = dict(_GOALS, workflows={"type": "list", "required": True})
    LIST_KEYS = ("goals", "workflows")

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """SDK checks plus list checks; repeats must be a whole number of at least 1."""
        errors = super().validate(params) + _list_errors(cls, params, cls.LIST_KEYS)
        r = params.get("repeats")
        if isinstance(r, (int, float)) and not isinstance(r, bool) and (r < 1 or int(r) != r):
            errors.append("%s: repeats must be a whole number >= 1" % cls.NAME)
        return errors

    def start(self, params, context):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """Build the goal order once; every planned task must be one the session can run."""
        missing = [t for t in params["goals"] if t not in context["tasks"]]
        if missing:
            raise ValueError("goals %s are not in the experiment's tasks" % missing)
        self.order = goal_order(params["goals"], self._workflows(params), params["repeats"], params["seed"])
        self.pause_s = self._pause(params)

    def _workflows(self, params):
        # type: (Dict[str, Any]) -> List[str]
        """Workflows run for every goal."""
        return list(params["workflows"])

    def _pause(self, params):
        # type: (Dict[str, Any]) -> Optional[float]
        """Planned pause between goals; None means no pause step."""
        return None

    def next_step(self, history):
        # type: (List[Any]) -> Any
        """Goal, then its pause (if any), until the order is used up; no pause after the last goal."""
        goals_done = sum(1 for o in history if o.step.kind == "goal")
        last_was_goal = bool(history) and history[-1].step.kind == "goal"
        if self.pause_s is not None and last_was_goal and goals_done < len(self.order):
            return wait(self.pause_s, self.NAME, meta={"rule": self.NAME})
        if goals_done >= len(self.order):
            return done()
        task, workflow = self.order[goals_done]
        return goal(task, workflow, meta={"slot": goals_done + 1})


class BackToBack(_OrderedGoals):
    """Goals one after another; only the safety guard's own rest separates them."""

    NAME = "back_to_back"


class FixedPause(_OrderedGoals):
    """Planned pause = gap_factor x tau_s; one gap factor per session."""

    NAME = "fixed_pause"
    PARAMS = dict(_OrderedGoals.PARAMS,
                  gap_factor={"type": "number", "required": True},
                  tau_s={"type": "number", "required": True},
                  tau_status={"choices": list(TAU_STATUS), "required": True},
                  tau_source={"type": "text", "required": True})

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """gap_factor >= 0 and tau_s > 0; a negative pause is never clamped."""
        errors = super().validate(params)
        if isinstance(params.get("gap_factor"), (int, float)) and params["gap_factor"] < 0:
            errors.append("fixed_pause: gap_factor must be >= 0")
        if isinstance(params.get("tau_s"), (int, float)) and params["tau_s"] <= 0:
            errors.append("fixed_pause: tau_s must be > 0")
        return errors

    def _pause(self, params):
        # type: (Dict[str, Any]) -> Optional[float]
        """gap_factor x tau_s; tau_status says whether tau is measured or a scheduling value."""
        return float(params["gap_factor"]) * float(params["tau_s"])


class MatchedThroughput(_OrderedGoals):
    """
    Planned pause G = 3600 * p / S - t for one workflow: p = success probability per submitted
    goal, t = mean wall seconds per submitted goal (retries included), S = target successful
    goals per hour. p and t come from a pilot stored in the profile (never reused in the session).
    """

    NAME = "matched_throughput"
    PARAMS = dict(_GOALS,
                  workflow={"choices": ["linear", "agentic"], "required": True},
                  target_successes_per_hour={"type": "number", "required": True},
                  pilot_success_probability={"type": "number", "required": True},
                  pilot_mean_goal_s={"type": "number", "required": True},
                  pilot_goal_ids={"type": "list", "required": True})
    LIST_KEYS = ("goals",)

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """p in (0, 1], t and S above 0, pilot goal ids listed."""
        errors = super().validate(params)
        p, t, s = (params.get(k) for k in ("pilot_success_probability", "pilot_mean_goal_s",
                                            "target_successes_per_hour"))
        if isinstance(p, (int, float)) and not 0 < p <= 1:
            errors.append("matched_throughput: pilot_success_probability must be in (0, 1]")
        if any(isinstance(v, (int, float)) and v <= 0 for v in (t, s)):
            errors.append("matched_throughput: pilot_mean_goal_s and target_successes_per_hour must be > 0")
        if not isinstance(params.get("pilot_goal_ids"), list) or not params.get("pilot_goal_ids"):
            errors.append("matched_throughput: pilot_goal_ids must list the pilot goals")
        return errors

    def _workflows(self, params):
        # type: (Dict[str, Any]) -> List[str]
        """One workflow per session: the pause is computed for that workflow's pilot values."""
        return [params["workflow"]]

    def _pause(self, params):
        # type: (Dict[str, Any]) -> Optional[float]
        """Computed once; below zero the session is refused (the target cannot be reached)."""
        gap = 3600.0 * params["pilot_success_probability"] / params["target_successes_per_hour"] \
            - params["pilot_mean_goal_s"]
        if gap < 0:
            raise ValueError("computed pause %.1f s is below zero: the target throughput cannot be reached" % gap)
        return gap


class LinearFirst(SessionPlanABC):
    """Per goal slot: linear; the agent only when linear failed, run straight after in the real sequence."""

    NAME = "linear_first"
    PARAMS = dict(_GOALS)

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """SDK checks plus the goal list."""
        return super().validate(params) + _list_errors(cls, params, ("goals",))

    def start(self, params, context):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """Slot order: tasks shuffled per repeat with seed + repeat."""
        missing = [t for t in params["goals"] if t not in context["tasks"]]
        if missing:
            raise ValueError("goals %s are not in the experiment's tasks" % missing)
        self.slots = [t for t, _ in goal_order(params["goals"], ["linear"], params["repeats"], params["seed"])]

    def next_step(self, history):
        # type: (List[Any]) -> Any
        """The agent follows a failed linear goal of the same slot; otherwise the next slot starts."""
        if history and history[-1].step.workflow == "linear" and history[-1].success is not True:
            slot = history[-1].step.meta["slot"]
            return goal(history[-1].step.task_id, "agentic", meta={"slot": slot, "agent_attempt": "run"})
        slots_done = sum(1 for o in history if o.step.workflow == "linear")
        if slots_done >= len(self.slots):
            return done()
        return goal(self.slots[slots_done], "linear", meta={"slot": slots_done + 1})


class CalibrationSteps(SessionPlanABC):
    """Hold load_task for hold_s, rest for rest_s; repeat. A step should be one long task (steady load)."""

    NAME = "calibration_steps"
    PARAMS = {
        "load_task": {"type": "text", "required": True},
        "workflow": {"choices": ["linear", "agentic"], "required": True},
        "hold_s": {"type": "number", "required": True},
        "rest_s": {"type": "number", "required": True},
        "repeats": {"type": "number", "required": True},
    }

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """hold_s and repeats above 0, rest_s at least 0."""
        errors = super().validate(params)
        if any(isinstance(params.get(k), (int, float)) and params[k] <= 0 for k in ("hold_s", "repeats")):
            errors.append("calibration_steps: hold_s and repeats must be > 0")
        if isinstance(params.get("rest_s"), (int, float)) and params["rest_s"] < 0:
            errors.append("calibration_steps: rest_s must be >= 0")
        return errors

    def start(self, params, context):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """The load task must be one the session can run."""
        if params["load_task"] not in context["tasks"]:
            raise ValueError("load_task %r is not in the experiment's tasks" % params["load_task"])
        self.p = params

    def next_step(self, history):
        # type: (List[Any]) -> Any
        """load_step, rest, load_step, ... ; no rest after the last step."""
        steps = sum(1 for o in history if o.step.kind == "load_step")
        if history and history[-1].step.kind == "load_step" and steps < int(self.p["repeats"]):
            return wait(self.p["rest_s"], "calibration rest", meta={"rule": self.NAME})
        if steps >= int(self.p["repeats"]):
            return done()
        return load_step(self.p["load_task"], self.p["workflow"], self.p["hold_s"], meta={"step": steps + 1})
