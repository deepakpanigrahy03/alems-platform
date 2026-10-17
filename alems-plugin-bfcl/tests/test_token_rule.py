"""E3 token based baseline: lowest predicted uncached tokens, same inputs as the controller."""

from types import SimpleNamespace

from alems_plugin_bfcl.decider import predict as R5
from alems_plugin_bfcl.decider.token_rule import TokenRule


def _fake_tokens(params, cls, option, calls_done):
    table = {"RB_cold": 100.0 + calls_done, "SR_warm": 300.0, "FR_warm": 50.0 + 100 * calls_done}
    return table.get(option)


def test_lowest_predicted_tokens_wins(monkeypatch):
    monkeypatch.setattr(R5, "predict_uncached_tokens", _fake_tokens, raising=False)
    t = TokenRule()
    assert t.choose("tool_error", 0)["option"] == "FR_warm"     # 50 < 100 < 300
    assert t.choose("tool_error", 5)["option"] == "RB_cold"     # 105 < 300 < 550


def test_missing_function_keeps_policy(monkeypatch):
    monkeypatch.delattr(R5, "predict_uncached_tokens", raising=False)
    res = TokenRule().choose("tool_error", 3)
    assert res["option"] is None and "missing" in res["reason"]


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
    assert rec["calls_done"] == 5 and "SR_warm" in rec["predicted_uncached_tokens"]
