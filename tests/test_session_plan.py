"""
Tests for the plan-driven session loop (core/execution/session_plan.py) and the
plan contract (alems_sdk.session_plan), with fake plans and fake execution.
"""
import pytest

from alems_sdk.session_plan import SessionPlanABC, done, goal, load_step, wait
from core.execution.session_plan import SessionPlanError, check_step, run_session_plan


class Refused(Exception):
    pass


class Fake(object):
    """Fake execution: a clock that moves with work and waits, goals solved per a script."""

    def __init__(self, solved=None, goal_s=10.0, raise_on=None):
        self.t, self.solved, self.goal_s, self.raise_on = 0.0, list(solved or []), goal_s, raise_on
        self.calls, self.finished, self.next_id = [], 0, 100

    def deps(self):
        return {"execute": self.execute, "result": self.result, "finish": self.finish, "wait": self.wait,
                "utc_ns": lambda: int(self.t * 1e9), "mono": lambda: self.t, "refusal": Refused}

    def execute(self, task_id, provider, workflow):
        self.calls.append((task_id, provider, workflow))
        if self.raise_on == len(self.calls):
            raise RuntimeError("adapter error")
        self.t += self.goal_s
        self.next_id += 1
        return self.next_id

    def result(self, goal_id):
        ok = self.solved.pop(0) if self.solved else True
        return {"success": ok, "attempt_ids": [goal_id * 10], "run_ids": [goal_id * 100],
                "first_run_start_ns": int((self.t - self.goal_s) * 1e9), "failure_reason": None if ok else "wrong"}

    def wait(self, seconds, label):
        self.t += seconds

    def finish(self):
        self.finished += 1


def plan_of(steps, params_spec=None, refuse=None):
    """Plan class that returns steps in order, recording the history it saw."""
    class P(SessionPlanABC):
        NAME = "scripted"
        PARAMS = params_spec or {}
        seen = []

        def start(self, params, context):
            if refuse:
                raise ValueError(refuse)
            self.context = context

        def next_step(self, history):
            P.seen.append(list(history))
            return steps[len(history)] if len(history) < len(steps) else done()
    return P


def test_goals_and_waits_in_order_with_outcomes():
    fake = Fake()
    steps = [goal("t1", "linear"), wait(5, "pause"), goal("t1", "agentic")]
    out = run_session_plan({"plugin": "scripted"}, ["t1"], ["p1"], fake.deps(), plan_cls=plan_of(steps))
    assert [o.step.kind for o in out] == ["goal", "wait", "goal"]
    assert fake.calls == [("t1", "p1", "linear"), ("t1", "p1", "agentic")]
    assert out[1].planned_s == 5.0 and out[1].actual_s == 5.0
    assert out[0].success is True and out[0].goal_ids == [101] and out[0].run_ids == [10100]
    assert fake.finished == 1


def test_plan_sees_failure_and_can_branch():
    fake = Fake(solved=[False])

    class LinearFirst(SessionPlanABC):
        NAME = "lf"

        def start(self, params, context):
            pass

        def next_step(self, history):
            if not history:
                return goal("t1", "linear")
            if len(history) == 1 and history[0].success is False:
                return goal("t1", "agentic")
            return done()
    out = run_session_plan({"plugin": "lf"}, ["t1"], ["p1"], fake.deps(), plan_cls=LinearFirst)
    assert [o.step.workflow for o in out] == ["linear", "agentic"]
    assert out[0].status == "failed" and out[0].failure_reason == "wrong"


def test_load_step_runs_until_hold_and_reports_it():
    fake = Fake(goal_s=10.0)
    out = run_session_plan({"plugin": "s"}, ["load"], ["p1"], fake.deps(),
                           plan_cls=plan_of([load_step("load", "linear", 35)]))
    assert len(fake.calls) == 4 and out[0].hold_achieved is True and out[0].actual_s == 40.0
    assert len(out[0].goal_ids) == 4 and out[0].planned_s == 35.0


def test_execution_error_is_an_outcome_not_a_crash():
    fake = Fake(raise_on=1)
    out = run_session_plan({"plugin": "s"}, ["t1"], ["p1"], fake.deps(),
                           plan_cls=plan_of([goal("t1", "linear"), goal("t1", "linear")]))
    assert out[0].status == "error" and "adapter error" in out[0].failure_reason
    assert out[1].status == "completed"


def test_guard_refusal_ends_the_session_and_still_finishes():
    fake = Fake()

    def refuse(*a):
        raise Refused("config_missing")
    d = fake.deps()
    d["execute"] = refuse
    with pytest.raises(Refused):
        run_session_plan({"plugin": "s"}, ["t1"], ["p1"], d, plan_cls=plan_of([goal("t1", "linear")]))
    assert fake.finished == 1


def test_bad_params_and_refusal_stop_before_any_step():
    fake = Fake()
    with pytest.raises(SessionPlanError, match="missing parameter gap"):
        run_session_plan({"plugin": "s", "params": {}}, ["t1"], ["p1"], fake.deps(),
                         plan_cls=plan_of([], {"gap": {"type": "number", "required": True}}))
    with pytest.raises(SessionPlanError, match="pause below zero"):
        run_session_plan({"plugin": "s"}, ["t1"], ["p1"], fake.deps(),
                         plan_cls=plan_of([], refuse="computed pause below zero"))
    assert fake.calls == []


def test_malformed_steps_are_refused():
    for bad in (goal("other", "linear"), goal("t1", "both"), wait(-1, "x"),
                load_step("t1", "linear", 1).__class__("load_step", task_id="t1", workflow="linear", hold_s=0),
                goal("t1", "linear", provider="nope")):
        with pytest.raises(SessionPlanError):
            check_step(bad, ["t1"], ["p1"])


def test_bool_is_not_a_number_in_params():
    P = plan_of([], {"gap": {"type": "number", "required": True}})
    assert P.validate({"gap": True}) and not P.validate({"gap": 0.3})
