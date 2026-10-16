import random

from alems_plugin_bfcl.decider import predict as P

PARAMS = {
    "classes": {
        "timeout": {
            "_x_star_calls": 5.0, "_budget_j": 10000.0,
            "options": {
                "SR_warm": {"alpha": 7.2, "beta": 2.8, "e2_a": 400.0, "e2_b": -20.0,
                            "p_repeat_after_fail": 0.1},
                "FR_warm": {"alpha": 9.1, "beta": 0.9, "e2_a": 2000.0, "e2_b": 0.0},
            },
        },
        "_default": {"_budget_j": 10000.0,
                     "options": {"FR_warm": {"alpha": 9, "beta": 1, "e2_a": 2000.0}}},
    }
}


def test_predict_energy_and_none():
    assert P.predict_energy(PARAMS, "timeout", "SR_warm", 5, 0, 0) == 300.0
    assert P.predict_energy(PARAMS, "timeout", "RB_warm", 5, 0, 0) is None


def test_decide_cheapest_then_escalates():
    c, _ = P.decide(PARAMS, "timeout", ["SR_warm", "FR_warm"], 5, 0, 0)
    assert c == "SR_warm"
    c, _ = P.decide(PARAMS, "timeout", ["SR_warm", "FR_warm"], 5, 0, 0, tried={"SR_warm": 1})
    assert c == "FR_warm"


def test_default_fallback_and_budget():
    c, _ = P.decide(PARAMS, "silent_detected", ["FR_warm"], 3, 0, 0)
    assert c == "FR_warm"
    tight = {"classes": {"_default": dict(PARAMS["classes"]["_default"], _budget_j=1.0)}}
    assert P.decide(tight, "x", ["FR_warm"], 3, 0, 0)[0] == "abandon"


def test_thompson_and_threshold():
    c, _ = P.decide(PARAMS, "timeout", ["SR_warm", "FR_warm"], 5, 0, 0, rng=random.Random(0))
    assert c in ("SR_warm", "FR_warm")
    assert P.restart_below_threshold(PARAMS, "timeout", 3) is True
    assert P.restart_below_threshold(PARAMS, "timeout", 8) is False
