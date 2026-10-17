"""laya_text v3 (shared by the decider and R7's export) and the laya_p gate pieces."""

import json
from types import SimpleNamespace

from alems_plugin_bfcl import laya_text as LT
from alems_plugin_bfcl.decider.adapter import ControllerEntry, failure_context_json

MSGS = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "Please start the engine."},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "startEngine", "arguments": "{\"x\":1}"}}]},
    {"role": "tool", "name": "startEngine", "content": "{\"error\": \"Must press the brake fully.\"}"},
    {"role": "assistant", "content": "Trying again\n" + LT.FIXED_NOTICE},
]


def test_context_from_agent_error_and_notice():
    ctx = LT.failure_context(MSGS, "tool_error", "ignored when the context has an error")
    assert ctx["error"] == "startEngine: Must press the brake fully."
    assert ctx["error_source"] == "tool_json" and ctx["notice_stripped"] is True
    assert ctx["last_user"] == "Please start the engine."
    assert ctx["last_agent"] == ["call startEngine({\"x\":1})", "Trying again"]
    assert MSGS[-1]["content"].endswith(LT.FIXED_NOTICE)       # input not mutated


def test_plan_labels_never_reach_the_record():
    assert LT.failure_context([], "persistent_tool_error", None)["cls"] == "tool_error"
    assert LT.failure_context([], "silent_tool_output", None)["cls"] == "silent_detected"
    assert LT.failure_context([], "persistent_wrong_model_step", None)["cls"] == "silent_detected"
    assert LT.failure_context([], "json_parse", None)["cls"] == "json_parse"


def test_harness_error_fallback():
    ctx = LT.failure_context([{"role": "user", "content": "u"}], "rate_limit",
                             "injected rate_limit 429")
    assert ctx["error"] == "harness: injected rate_limit 429"
    assert ctx["error_source"] == "attempt1_error_message"


def test_round_trip_byte_identical():
    ctx = LT.failure_context(MSGS, "tool_error", None)
    js = LT.context_json(ctx)
    again = LT.context_json(json.loads(js))                  # export side: stored payload
    assert js == again
    assert LT.laya_text(js, "SR_warm", 2) == LT.laya_text(again, "SR_warm", 2)


def test_no_point_and_header_shape():
    js = LT.context_json(LT.failure_context(MSGS, "tool_error", None))
    text = LT.laya_text(js, "RB_cold", 3)
    assert text.startswith("decision: ear_recovery_success\nclass: tool_error | option: RB,cold | attempt: 3")
    assert "point" not in text and "early" not in text and "late" not in text


def test_character_budget_respected():
    long = {"role": "user", "content": "x" * 50000}
    js = LT.context_json(LT.failure_context([long], "timeout", "e" * 5000))
    text = LT.laya_text(js, "FR_warm", 2)
    assert (len(text) + LT.CHARS_PER_TOKEN - 1) // LT.CHARS_PER_TOKEN <= LT.BUDGET_TOTAL + 8
    assert LT.TRUNC_MARK in text


def test_adapter_texts_equal_recompute(monkeypatch):
    monkeypatch.delenv("ALEMS_RAF_LAYA_URL", raising=False)
    monkeypatch.delenv("ALEMS_RAF_CTRL_PARAMS", raising=False)
    f = {"cls": "tool_error", "kind": "tool", "llm_ordinal": 2, "tool_ordinal": 1,
         "messages": MSGS, "error_message": "tool failed"}
    ex = SimpleNamespace(_bfcl_session=SimpleNamespace(failure=f, calls=[]),
                         _raf_population={"arm": "ctrl_laya", "study_id": "s", "study_seed": 1,
                                          "seed_key": "t|tool_error|late|1"})
    ControllerEntry("laya")(2, ex)
    rec = ex._raf_decision_detail[2]
    js = failure_context_json(ex, "tool_error")
    assert rec["laya_template_version"] == "v3"
    assert rec["laya_ctx_sha256"] == LT.sha256_text(js)
    for opt, h in rec["laya_text_sha256"].items():
        assert h == LT.sha256_text(LT.laya_text(js, opt, 2))
    assert rec["decider_used"] == "beta_p" and "not configured" in rec["fallback_reason"]
