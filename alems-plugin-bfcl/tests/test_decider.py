"""R6 decider tests: no GPU, no store, no network (laya is a fake)."""

import time
from types import SimpleNamespace

import pytest

from alems_plugin_bfcl.decider.adapter import ControllerEntry
from alems_plugin_bfcl.decider.base import ABANDON, OPTIONS, FailureState, check_state_dict
from alems_plugin_bfcl.decider.beta_p import BetaP
from alems_plugin_bfcl.decider.failclosed import FailClosed
from alems_plugin_bfcl.decider.laya_p import LayaP, LayaUnavailable


def params(**over):
    cls = {o: {"alpha": 5.0, "beta": 5.0, "e2_a": {"value": 10.0}, "e2_b": {"value": 1.0}}
           for o in OPTIONS if o != ABANDON}
    cls["SR_warm"].update(alpha=9.0, beta=1.0)
    p = {"params_version": "t1", "classes": {"timeout": cls},
         "x_star_calls": {"value": 3.0}, "budget": {"value_j": 1000.0}}
    p.update(over)
    return p


def state(**kw):
    base = dict(failure_class="timeout", attempt=2, calls_done=5, tokens_context=100,
                tokens_cached=50, joules_done=None, work_source="calls", turns_done=1,
                turn_starts=(0, 2), previous_options=())
    base.update(kw)
    return FailureState(**base)


def test_picks_lowest_j():
    assert BetaP(params()).decide(state(), list(OPTIONS)).option == "SR_warm"


def test_below_x_star_restart_only():
    d = BetaP(params()).decide(state(calls_done=1), list(OPTIONS))
    assert d.option.startswith("FR_")


def test_failed_option_dropped_when_next_rung_better():
    p = params()
    p["classes"]["timeout"]["SR_warm"]["p_repeat_after_fail"] = 0.1
    d = BetaP(p).decide(state(previous_options=("SR_warm",)), list(OPTIONS))
    assert d.option != "SR_warm"
    assert all(s.option != "SR_warm" for s in d.scores)


def test_failed_option_repeated_when_still_best():
    # SR_warm after one failure: p 9/11, J 18.3 beats roll back J 30 (R5 6.3).
    d = BetaP(params()).decide(state(previous_options=("SR_warm",)), list(OPTIONS))
    assert d.option == "SR_warm"


def test_hung_call_does_not_block_next_decision():
    calls = []
    resp = {"calibration": "ts1", "p": {o: 0.5 for o in OPTIONS if o != ABANDON}}

    def post(url, body, t):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(3.0)              # first call hangs past the deadline
        return resp

    beta = BetaP(params())
    fc = FailClosed(LayaP(beta, "http://x", post=post), beta, deadline_s=0.5)
    assert fc.decide(state(), list(OPTIONS)).fallback_reason == "timeout"
    second = fc.decide(state(), list(OPTIONS))
    assert second.fallback_reason is None and second.decider == "laya_p"


def test_failed_option_p_drops():
    b = BetaP(params())
    assert b.p(state(previous_options=("SR_warm",)), "SR_warm") < b.p(state(), "SR_warm")


def test_abandon_over_budget():
    d = BetaP(params(budget={"value_j": 1.0})).decide(state(), list(OPTIONS))
    assert d.option == ABANDON


def test_update_learns():
    b = BetaP(params())
    from alems_plugin_bfcl.decider.base import Outcome
    b.update(Outcome("timeout", ("SR_warm", "FR_cold"), True, False, 3))
    assert b.post[("timeout", "SR_warm")] == [9.0, 2.0]
    assert b.post[("timeout", "FR_cold")] == [6.0, 5.0]


def test_forbidden_inputs_rejected():
    for k in ("task_id", "occupancy_fraction", "pass_fail", "early"):
        with pytest.raises(ValueError):
            check_state_dict({k: 1})


def _laya(resp=None, sleep=0.0, exc=None):
    def post(url, body, t):
        if sleep:
            time.sleep(sleep)
        if exc:
            raise exc
        return resp
    return post


def test_laya_overrides_p():
    resp = {"calibration": "ts1", "p": {o: 0.1 for o in OPTIONS if o != ABANDON}}
    resp["p"]["RB_cold"] = 0.99
    d = LayaP(BetaP(params()), "http://x", post=_laya(resp)).decide(state(), list(OPTIONS))
    assert d.option == "RB_cold" and d.decider == "laya_p"


@pytest.mark.parametrize("post", [
    _laya(sleep=3.0, resp={}),
    _laya(exc=ConnectionRefusedError("down")),
    _laya(resp={"p": {}}),                       # uncalibrated
])
def test_fail_closed(post):
    beta = BetaP(params())
    d = FailClosed(LayaP(beta, "http://x", post=post), beta, deadline_s=0.5).decide(
        state(), list(OPTIONS))
    assert d.fallback_reason and d.decider == "beta_p"


def test_adapter_records_detail_and_learns(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    sess = SimpleNamespace(failure={"cls": "default", "kind": "llm", "turn": 1,
                                    "turn_starts": [0, 2], "llm_ordinal": 4,
                                    "messages": [{"role": "user", "content": "u"},
                                                 {"role": "assistant", "content": "a"}]},
                           calls=[{"prompt_tokens": 80, "cached_tokens": 40}])
    ex = SimpleNamespace(_bfcl_session=sess)
    e = ControllerEntry("beta")
    opt = e(2, ex)
    rec = ex._raf_decision_detail[2]
    assert rec["option"] == opt and rec["work_source"] == "calls"
    assert rec["failure_class"] == "default" and rec["calls_done"] == 4
    assert rec["params_version"] == "STUB_NOT_FOR_E3"
    e.update_from_goal(ex, None)                 # needs review: no change
    e.update_from_goal(ex, True)
    assert e._decider().fallback.post[("default", opt)][0] == 2.0


def test_laya_entry_without_url_fails_closed(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_LAYA_URL", raising=False)
    sess = SimpleNamespace(failure={"cls": "default", "kind": "llm"}, calls=[])
    ex = SimpleNamespace(_bfcl_session=sess)
    ControllerEntry("laya")(2, ex)
    assert "not configured" in ex._raf_decision_detail[2]["fallback_reason"]
