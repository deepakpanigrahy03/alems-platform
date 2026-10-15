"""Tests for the RAF R1 validity codes (spec R1 section 4.4)."""

import sqlite3

from alems_plugin_bfcl import raf_validity as V


def _conn(attempts, log):
    """In memory store with only the columns pilot_validity reads."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE goal_attempt (goal_id, attempt_id, attempt_number, outcome)")
    c.execute("CREATE TABLE failure_injection_log (goal_id, attempt_id, status, "
              "injected_type, target_phase, target_step)")
    c.executemany("INSERT INTO goal_attempt VALUES (1,?,?,?)", attempts)
    c.executemany("INSERT INTO failure_injection_log VALUES (1,?,?,?,?,?)", log)
    return c


def _plan(cls, ordinal=3, phase="tool", policy="FR_warm"):
    return {"policy": policy, "cls": cls, "phase": phase, "ordinal": ordinal}


def _checks(*fails):
    return [{"turn": t, "ok": t not in fails, "ms": 1.0} for t in range(3)]


ATT = [(11, 1, "failure"), (12, 2, "success")]


def test_silent_detected_is_valid():
    c = _conn(ATT, [(11, "injected", "silent_tool_output", "tool", 3)])
    meta = {1: {"corruption": {"applied": True}, "turn_checks": _checks(1)}}
    assert V.classify(c, 1, _plan("silent_tool_output"), attempt_meta=meta)["codes"] == []


def test_detection_missed():
    c = _conn(ATT, [(11, "injected", "wrong_model_step", "tool", 3)])
    meta = {1: {"corruption": {"applied": True}, "turn_checks": _checks()}}
    codes = V.classify(c, 1, _plan("wrong_model_step"), attempt_meta=meta)["codes"]
    assert V.DETECTION_MISSED in codes


def test_corruption_not_applied():
    c = _conn(ATT, [(11, "injected", "silent_tool_output", "tool", 3)])
    meta = {1: {"corruption": {"applied": False, "reason": "error_result"}}}
    codes = V.classify(c, 1, _plan("silent_tool_output"), attempt_meta=meta)["codes"]
    assert V.CORRUPTION_NOT_APPLIED in codes


def test_false_detection_on_b0():
    c = _conn([(11, 1, "success")], [])
    meta = {1: {"turn_checks": _checks(2)}}
    codes = V.classify(c, 1, {"policy": "B0"}, attempt_meta=meta)["codes"]
    assert codes == [V.FALSE_DETECTION]


def test_b0_clean_checks_valid():
    c = _conn([(11, 1, "success")], [])
    meta = {1: {"turn_checks": _checks()}}
    assert V.classify(c, 1, {"policy": "B0"}, attempt_meta=meta)["codes"] == []


def test_persistent_fires_in_both_attempts():
    cls = "persistent_rate_limit"
    log = [(11, "injected", cls, "llm", 2), (12, "injected", cls, "llm", 2)]
    c = _conn(ATT, log)
    assert V.classify(c, 1, _plan(cls, 2, "llm"))["codes"] == []


def test_persistent_missing_second_firing():
    cls = "persistent_tool_error"
    c = _conn(ATT, [(11, "injected", cls, "tool", 3)])
    assert V.classify(c, 1, _plan(cls))["codes"] == [V.INJECTION_MISMATCH]


def test_clean_class_unchanged_without_meta():
    c = _conn(ATT, [(11, "injected", "tool_error", "tool", 3)])
    assert V.classify(c, 1, _plan("tool_error"))["codes"] == []
