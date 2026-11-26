"""
Tests for the thermal session plans (extensions/thermal_exposure/pacing.py), run
through the generic session loop with fake execution.
"""
import pytest

from core.execution.session_plan import SessionPlanError, run_session_plan
from extensions.thermal_exposure.pacing import (BackToBack, CalibrationSteps, FixedPause, LinearFirst,
                                                MatchedThroughput, goal_order)


class Fake(object):
    """Fake execution: each goal takes goal_s; success per a script (default solved)."""

    def __init__(self, solved=None, goal_s=10.0):
        self.t, self.solved, self.goal_s, self.calls, self.next_id = 0.0, list(solved or []), goal_s, [], 0

    def deps(self):
        return {"execute": self.execute, "result": self.result, "finish": lambda: None, "wait": self.wait,
                "utc_ns": lambda: int(self.t * 1e9), "mono": lambda: self.t, "refusal": KeyError}

    def execute(self, task_id, provider, workflow):
        self.calls.append((task_id, workflow))
        self.t += self.goal_s
        self.next_id += 1
        return self.next_id

    def result(self, goal_id):
        ok = self.solved.pop(0) if self.solved else True
        return {"success": ok, "attempt_ids": [goal_id], "run_ids": [goal_id],
                "first_run_start_ns": int((self.t - self.goal_s) * 1e9), "last_run_end_ns": int(self.t * 1e9),
                "failure_reason": None if ok else "wrong"}

    def wait(self, seconds, label):
        self.t += seconds


class Recorder(object):
    """Fake recording: collects plan spans and the plan identity; session time = wall time + 1000 ns."""

    def __init__(self):
        self.spans, self.plans = [], []

    def session_ns(self, wall_ns):
        return wall_ns + 1000

    def add_plan_span(self, kind, name, start_ns, end_ns, attrs):
        self.spans.append((kind, name, start_ns, end_ns, attrs))
        return True

    def record_plan(self, profile_id, sha, plugin, params):
        self.plans.append((profile_id, sha, plugin, dict(params)))


def run(cls, params, tasks=("a", "b"), fake=None, recorder=None, config_path=None):
    fake = fake or Fake()
    out = run_session_plan({"plugin": cls.NAME, "params": params}, list(tasks), ["p"], fake.deps(), plan_cls=cls,
                           recorder=recorder, config_path=config_path)
    return out, fake


GOALS = {"goals": ["a", "b"], "workflows": ["linear", "agentic"], "repeats": 2, "seed": 7}


def test_goal_order_is_reproducible_and_balanced():
    o1, o2 = goal_order(["a", "b"], ["linear", "agentic"], 2, 7), goal_order(["a", "b"], ["linear", "agentic"], 2, 7)
    assert o1 == o2 and len(o1) == 8 and sorted(o1[:4]) == sorted(o1[4:])


def test_back_to_back_has_no_planned_pause():
    out, fake = run(BackToBack, GOALS)
    assert [o.step.kind for o in out] == ["goal"] * 8 and len(fake.calls) == 8


def test_fixed_pause_between_goals_but_not_after_the_last():
    p = dict(GOALS, gap_factor=0.3, tau_s=30, tau_status="PILOT_ONLY", tau_source="pilot scheduling value")
    out, _ = run(FixedPause, p)
    kinds = [o.step.kind for o in out]
    assert kinds == ["goal", "wait"] * 7 + ["goal"]
    assert all(o.planned_s == pytest.approx(9.0) for o in out if o.step.kind == "wait")


def test_fixed_pause_rejects_bad_tau_and_negative_gap():
    p = dict(GOALS, gap_factor=-1, tau_s=0, tau_status="GUESS", tau_source="x")
    with pytest.raises(SessionPlanError) as e:
        run(FixedPause, p)
    assert "gap_factor" in str(e.value) and "tau_s" in str(e.value) and "tau_status" in str(e.value)


def test_matched_throughput_gap_and_refusal():
    p = {"goals": ["a"], "repeats": 3, "seed": 1, "workflow": "agentic", "target_successes_per_hour": 20,
         "pilot_success_probability": 0.9, "pilot_mean_goal_s": 120, "pilot_goal_ids": [1, 2]}
    out, fake = run(MatchedThroughput, p)
    assert [w for _, w in fake.calls] == ["agentic"] * 3
    assert [o.planned_s for o in out if o.step.kind == "wait"] == [pytest.approx(42.0)] * 2
    with pytest.raises(SessionPlanError, match="below zero"):
        run(MatchedThroughput, dict(p, target_successes_per_hour=40))


def test_linear_first_runs_agent_only_after_a_linear_failure():
    p = {"goals": ["a", "b"], "repeats": 1, "seed": 3}
    out, fake = run(LinearFirst, p, fake=Fake(solved=[False, True, True]))
    assert [w for _, w in fake.calls] == ["linear", "agentic", "linear"]
    assert out[1].step.task_id == out[0].step.task_id and out[1].step.meta["slot"] == 1


def test_calibration_holds_and_rests():
    p = {"load_task": "a", "workflow": "linear", "channel": "gpu", "level": 1.0, "hold_s": 25, "rest_s": 60,
         "repeats": 2}
    out, fake = run(CalibrationSteps, p, fake=Fake(goal_s=30.0))
    assert [o.step.kind for o in out] == ["load_step", "wait", "load_step"]
    assert all(o.hold_achieved for o in out if o.step.kind == "load_step") and len(fake.calls) == 2


def test_unknown_task_refused_before_any_step():
    with pytest.raises(SessionPlanError, match="not in the experiment's tasks"):
        run(BackToBack, dict(GOALS, goals=["a", "zzz"]))


def test_fixed_pause_labels_the_recording_and_records_the_profile(tmp_path):
    prof = tmp_path / "fixed_pause_pilot.yaml"
    prof.write_text("session_plan: {}\n")
    rec = Recorder()
    p = dict(GOALS, repeats=1, gap_factor=0.5, tau_s=30, tau_status="PILOT_ONLY", tau_source="pilot value")
    run(FixedPause, p, recorder=rec, config_path=str(prof))
    assert rec.plans[0][0] == "fixed_pause_pilot" and len(rec.plans[0][1]) == 64
    assert rec.plans[0][2] == "fixed_pause" and rec.plans[0][3]["tau_status"] == "PILOT_ONLY"
    kinds = [s[0] for s in rec.spans]
    assert kinds.count("goal_slot") == 4 and kinds.count("pause") == 3
    pause = next(s for s in rec.spans if s[0] == "pause")
    assert pause[4] == {"planned_s": 15.0, "actual_s": 15.0, "rule": "fixed_pause"}
    slot = next(s for s in rec.spans if s[0] == "goal_slot")
    assert slot[3] - slot[2] == 10 * 10 ** 9 and slot[4]["success"] is True


def test_linear_first_slot_spans_cover_linear_and_agent():
    rec = Recorder()
    run(LinearFirst, {"goals": ["a", "b"], "repeats": 1, "seed": 3}, recorder=rec, fake=Fake(solved=[False, True, True]))
    slots = [s for s in rec.spans if s[0] == "goal_slot"]
    assert [s[4]["agent_attempt"] for s in slots] == ["run", "not_run"]
    assert slots[0][4]["attempt_run_ids"] == [1, 2] and slots[0][3] - slots[0][2] == 20 * 10 ** 9


def test_calibration_step_spans_and_no_recording_is_fine():
    rec = Recorder()
    p = {"load_task": "a", "workflow": "linear", "channel": "gpu", "level": 1.0, "hold_s": 25, "rest_s": 60,
         "repeats": 2}
    run(CalibrationSteps, p, recorder=rec, fake=Fake(goal_s=30.0))
    steps = [s for s in rec.spans if s[0] == "calibration_step"]
    assert len(steps) == 2 and steps[0][4] == {"channel": "gpu", "level": 1.0, "hold_s": 25.0, "hold_achieved": True}
    run(CalibrationSteps, p, recorder=None, fake=Fake(goal_s=30.0))   # no recording: plan still runs
