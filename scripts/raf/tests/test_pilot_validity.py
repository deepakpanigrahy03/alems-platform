"""Tests for scripts/raf/pilot_validity.py on an in memory store."""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pilot_validity as pv  # noqa: E402

PLAN = {"policy": "FR_cold", "cls": "timeout", "phase": "tool", "ordinal": 2}


def _db(attempts, log):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE goal_attempt (attempt_id, goal_id, attempt_number, outcome)")
    c.execute("CREATE TABLE failure_injection_log (attempt_id, goal_id, status, "
              "injected_type, target_phase, target_step)")
    c.executemany("INSERT INTO goal_attempt VALUES (?,1,?,?)", attempts)
    c.executemany("INSERT INTO failure_injection_log VALUES (?,1,?,?,?,?)", log)
    return c


OK_ATT = [(10, 1, "failure"), (11, 2, "success")]
FIRE = (10, "injected", "timeout", "tool", 2)
POST = (11, "eligible", None, "post_harness", 0)


def test_valid_execution():
    assert pv.classify(_db(OK_ATT, [FIRE, POST]), 1, PLAN)["codes"] == []


def test_fired_in_attempt_two_is_mismatch():
    log = [FIRE, (11, "injected", "timeout", "tool", 2)]
    assert pv.classify(_db(OK_ATT, log), 1, PLAN)["codes"] == [pv.INJECTION_MISMATCH]


def test_wrong_ordinal_is_mismatch():
    log = [(10, "injected", "timeout", "tool", 3)]
    assert pv.classify(_db(OK_ATT, log), 1, PLAN)["codes"] == [pv.INJECTION_MISMATCH]


def test_no_recovery_attempt_is_mismatch():
    db = _db([(10, 1, "failure")], [FIRE])
    assert pv.classify(db, 1, PLAN)["codes"] == [pv.INJECTION_MISMATCH]


def test_not_reached():
    log = [(10, "target_not_reached", None, "tool", 2)]
    assert pv.classify(_db(OK_ATT, log), 1, PLAN)["codes"] == [pv.INJECTION_NOT_REACHED]


def test_pre_injection_failure():
    assert pv.classify(_db(OK_ATT, []), 1, PLAN)["codes"] == [pv.PRE_INJECTION_FAILURE]


def test_prepare_codes_carried_and_b0():
    prep = {1: {"validity": []}, 2: {"validity": ["CACHE_RESET_FAILED"]}}
    out = pv.classify(_db(OK_ATT, [FIRE]), 1, PLAN, prep)
    assert out["codes"] == ["CACHE_RESET_FAILED"]
    b0 = pv.classify(_db([(10, 1, "success")], []), 1, {"policy": "B0"})
    assert b0["codes"] == []
