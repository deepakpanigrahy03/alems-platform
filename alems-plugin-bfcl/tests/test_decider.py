"""R6 decider tests (R5 predict.py layout, observable class): no GPU, store or network."""

import time
from types import SimpleNamespace

import pytest

from alems_plugin_bfcl.decider.adapter import ControllerEntry, decision_seed
from alems_plugin_bfcl.decider.base import (ABANDON, OPTIONS, FailureState,
                                            check_state_dict, observable_class)
from alems_plugin_bfcl.decider.beta_p import BetaP
from alems_plugin_bfcl.decider.failclosed import FailClosed
from alems_plugin_bfcl.decider.laya_p import LayaP
from alems_plugin_bfcl.decider.state import build_state

K = 1000.0          # strong priors: samples sit near the mean, choices are stable


def params(x_star=3.0, budget=1000.0, **opt_over):
    opts = {o: {"alpha": 5 * K, "beta": 5 * K, "e2_a": 10.0, "e2_b": 1.0}
            for o in OPTIONS if o != ABANDON}
    opts["SR_warm"].update(alpha=9 * K, beta=1 * K)
    for o, upd in opt_over.items():
        opts[o].update(upd)
    return {"params_version": "t1", "data_tag": "d1",
            "classes": {"timeout": {"_x_star_calls": x_star, "_budget_j": budget,
                                    "options": opts}}}


def state(**kw):
    base = dict(failure_class="timeout", attempt=2, calls_done=5, tokens_context=100,
                tokens_cached=50, joules_done=None, work_source="calls", turns_done=1,
                turn_starts=(0, 2), previous_options=())
    base.update(kw)
    return FailureState(**base)


def test_picks_lowest_j():
    assert BetaP(params()).decide(state(), list(OPTIONS), seed=1).option == "SR_warm"


def test_below_x_star_restart_only():
    d = BetaP(params()).decide(state(calls_done=1), list(OPTIONS), seed=1)
    assert d.option.startswith("FR_")


def test_failed_option_dropped_when_next_rung_better():
    p = params(SR_warm={"p_repeat_after_fail": 0.1})
    d = BetaP(p).decide(state(previous_options=("SR_warm",)), list(OPTIONS), seed=1)
    assert d.option != "SR_warm"
    assert all(s.option != "SR_warm" for s in d.scores)


def test_failed_option_repeated_when_still_best():
    d = BetaP(params()).decide(state(previous_options=("SR_warm",)), list(OPTIONS), seed=1)
    assert d.option == "SR_warm"


def test_abandon_over_class_budget():
    d = BetaP(params(budget=1.0)).decide(state(), list(OPTIONS), seed=1)
    assert d.option == ABANDON


def test_energy_from_r5_predict_with_tokens():
    p = params(SR_warm={"e2_g": 0.1})
    b = BetaP(p)
    assert b.energy(state(), "SR_warm") == pytest.approx(10.0 + 5 + 0.1 * 100)
    assert b.energy(state(), "FR_warm") == pytest.approx(15.0)


def test_unfitted_class_uses_default():
    p = params()
    p["classes"]["_default"] = {"_budget_j": 1000.0,
                                "options": {"FR_cold": {"alpha": 1.0, "beta": 1.0,
                                                        "e2_a": 3.0, "e2_b": 0.0}}}
    b = BetaP(p)
    s = state(failure_class="silent_detected")
    assert b.energy(s, "FR_cold") == pytest.approx(3.0)
    assert b.energy(s, "SR_warm") is None


def test_seeded_sampling_reproducible():
    weak = params()
    for v in weak["classes"]["timeout"]["options"].values():
        v.update(alpha=2.0, beta=2.0)
    b = BetaP(weak)
    a1 = [s.p for s in b.decide(state(), list(OPTIONS), seed=7).scores]
    a2 = [s.p for s in b.decide(state(), list(OPTIONS), seed=7).scores]
    a3 = [s.p for s in b.decide(state(), list(OPTIONS), seed=8).scores]
    assert a1 == a2 and a1 != a3


def test_counts_shift_choice():
    counts = {"timeout|SR_warm": [0, 20 * K], "timeout|RB_warm": [20 * K, 0]}
    d = BetaP(params()).decide(state(), list(OPTIONS), counts=counts, seed=1)
    assert d.option == "RB_warm"


def test_inner_loop_lowers_posterior():
    b = BetaP(params())
    a0, b0 = b.posterior(state(), "SR_warm", {})
    a1, b1 = b.posterior(state(previous_options=("SR_warm",)), "SR_warm", {})
    assert a1 == a0 and b1 == b0 + 1


def test_forbidden_inputs_rejected():
    for k in ("task_id", "occupancy_fraction", "pass_fail", "early", "plan_cls"):
        with pytest.raises(ValueError):
            check_state_dict({k: 1})


def test_observable_class_strips_persistent():
    assert observable_class("persistent_timeout") == "timeout"
    assert observable_class("silent_detected") == "silent_detected"
    assert observable_class("tool_error") == "tool_error"


@pytest.mark.parametrize("bad", ["persistent_timeout", "silent_tool_output", "wrong_model_step"])
def test_plan_only_class_refused(bad):
    with pytest.raises(ValueError):
        state(failure_class=bad)


def test_builder_never_passes_plan_label():
    sess = SimpleNamespace(failure={"cls": "persistent_tool_error", "kind": "tool",
                                    "llm_ordinal": 4, "tool_ordinal": 3},
                           calls=[{"prompt_tokens": 80, "cached_tokens": 40}])
    s = build_state(SimpleNamespace(_bfcl_session=sess), 2, ())
    assert s.failure_class == "tool_error"
    assert s.calls_done == 7 and s.model_calls_done == 4


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
        state(), list(OPTIONS), seed=3)
    assert d.fallback_reason and d.decider == "beta_p"


def test_hung_call_does_not_block_next_decision():
    calls = []
    resp = {"calibration": "ts1", "p": {o: 0.5 for o in OPTIONS if o != ABANDON}}

    def post(url, body, t):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(3.0)
        return resp

    beta = BetaP(params())
    fc = FailClosed(LayaP(beta, "http://x", post=post), beta, deadline_s=0.5)
    assert fc.decide(state(), list(OPTIONS)).fallback_reason == "timeout"
    second = fc.decide(state(), list(OPTIONS))
    assert second.fallback_reason is None and second.decider == "laya_p"


def _ex(hist_calls):
    sess = SimpleNamespace(failure={"cls": "default", "kind": "llm", "turn": 1,
                                    "turn_starts": [0, 2], "llm_ordinal": 4,
                                    "tool_ordinal": 2},
                           calls=[{"prompt_tokens": 80, "cached_tokens": 40}])

    def hist(pop):
        hist_calls.append(pop)
        return {"counts": {"default|FR_cold": [3, 1]}, "cut_id": 42,
                "goals": 4, "query_ns": 1000}
    return SimpleNamespace(_bfcl_session=sess, _raf_history=hist,
                           _raf_context={"goal_key": "g1"},
                           _raf_population={"arm": "ctrl", "study_id": "e3",
                                            "study_seed": 99})


def test_adapter_records_population_seed_counts(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    seen = []
    ex = _ex(seen)
    opt = ControllerEntry("beta")(2, ex)
    rec = ex._raf_decision_detail[2]
    assert rec["option"] == opt
    assert {k: rec[k] for k in ("params_version", "data_tag", "decider", "arm", "study_id")} == {
        "params_version": "STUB_NOT_FOR_E3", "data_tag": "STUB", "decider": "beta_p",
        "arm": "ctrl", "study_id": "e3"}
    assert rec["seed"] == decision_seed(99, "g1", 2)
    assert rec["counts"] == {"default|FR_cold": [3, 1]} and rec["cut_id"] == 42
    assert all(s["p"] is not None for s in rec["scores"])
    assert rec["failure_class"] == "default" and rec["calls_done"] == 6


def test_seed_from_fixed_goal_identity(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    seeds = []
    for goal_key in ("g1_aaaa", "g1_bbbb"):          # same goal, new random suffix
        ex = _ex([])
        ex._raf_context = {"goal_key": goal_key}
        ex._raf_population = dict(ex._raf_population, seed_key="bfcl_t|timeout|late|1")
        ControllerEntry("beta")(2, ex)
        seeds.append(ex._raf_decision_detail[2]["seed"])
    assert seeds[0] == seeds[1] == decision_seed(99, "bfcl_t|timeout|late|1|ctrl", 2)


def test_history_read_once_per_goal(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    seen = []
    ex = _ex(seen)
    e = ControllerEntry("beta")
    e(2, ex)
    e(3, ex)
    assert len(seen) == 1
    assert ex._raf_decision_detail[3]["cut_id"] == 42


def test_laya_entry_without_url_fails_closed(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_LAYA_URL", raising=False)
    ex = _ex([])
    ControllerEntry("laya")(2, ex)
    rec = ex._raf_decision_detail[2]
    assert "not configured" in rec["fallback_reason"] and rec["decider"] == "laya_p"
    assert rec["decider_used"] == "beta_p"
