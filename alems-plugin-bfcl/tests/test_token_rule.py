"""E3 baselines: token_rule (lowest predicted uncached tokens) and xstar_rule (threshold)."""

from types import SimpleNamespace

from alems_plugin_bfcl.decider import predict as R5
from alems_plugin_bfcl.decider.token_rule import TokenRule
from alems_plugin_bfcl.decider.xstar_rule import XStarRule


def _fake_tokens(params, cls, option, calls_done):
    table = {"RB_cold": 100.0 + calls_done, "SR_warm": 300.0, "FR_warm": 50.0 + 100 * calls_done}
    return table.get(option)


def _with_params(rule, params):
    rule._params()                     # loads the stub, then replace its params
    rule._beta.params = params
    return rule


PARAMS = {"classes": {
    "tool_error": {"_x_star_calls": 4.0, "options": {
        "FR_warm": {"alpha": 1, "beta": 1, "tok_a": 50.0, "tok_b": 100.0},
        "RB_cold": {"alpha": 1, "beta": 1, "tok_a": 100.0, "tok_b": 1.0},
        "SR_warm": {"alpha": 1, "beta": 1, "tok_a": 300.0, "tok_b": 0.0}}},
    "_default": {"_x_star_calls": 2.0, "options": {}}}}


def test_lowest_predicted_tokens_wins(monkeypatch):
    monkeypatch.setattr(R5, "predict_uncached_tokens", _fake_tokens, raising=False)
    t = TokenRule()
    assert t.choose("tool_error", 0)["option"] == "FR_warm"     # 50 < 100 < 300
    assert t.choose("tool_error", 5)["option"] == "RB_cold"     # 105 < 300 < 550


def test_params_fallback_when_r5_function_missing(monkeypatch):
    monkeypatch.delattr(R5, "predict_uncached_tokens", raising=False)
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    t = _with_params(TokenRule(), PARAMS)
    res = t.choose("tool_error", 5)
    assert res["option"] == "RB_cold" and res["source"] == "params_tok_a_tok_b"
    assert t.choose("json_parse", 5)["option"] is None          # _default has no tokens


def test_call_records_population_and_uses_observable_class(monkeypatch):
    monkeypatch.setattr(R5, "predict_uncached_tokens", _fake_tokens, raising=False)
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    sess = SimpleNamespace(failure={"cls": "persistent_tool_error", "kind": "tool",
                                    "llm_ordinal": 3, "tool_ordinal": 2}, calls=[])
    ex = SimpleNamespace(_bfcl_session=sess,
                         _raf_population={"arm": "token_rule", "study_id": "e3"})
    opt = TokenRule()(2, ex)
    rec = ex._raf_decision_detail[2]
    assert opt == "RB_cold" and rec["option"] == "RB_cold"
    assert rec["decider"] == "token_rule" and rec["failure_class"] == "tool_error"
    assert rec["calls_done"] == 5 and rec["token_source"] == "predict_uncached_tokens"


def test_xstar_rule_restart_below_threshold(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    x = _with_params(XStarRule(), PARAMS)
    assert x.choose("tool_error", 3)["option"] == "FR_warm"     # 3 < 4
    assert x.choose("tool_error", 4)["option"] == "SR_warm"
    assert x.choose("json_parse", 1)["option"] == "FR_warm"     # _default x* = 2


def test_xstar_rule_without_threshold_resumes(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    x = _with_params(XStarRule(), {"classes": {}})
    res = x.choose("tool_error", 0)
    assert res["option"] == "SR_warm" and res["reason"] == "no x* for class"
