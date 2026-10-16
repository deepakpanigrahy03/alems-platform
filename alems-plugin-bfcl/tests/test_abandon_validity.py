"""R6: an abandoned goal is a valid failed outcome, never INJECTION_MISMATCH."""

import sqlite3

from alems_plugin_bfcl import raf_validity as rv


def _conn(rows, log):
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE goal_attempt (attempt_id INTEGER PRIMARY KEY, goal_id INT,
            attempt_number INT, outcome TEXT, pass_fail INT);
        CREATE TABLE failure_injection_log (attempt_id INT, goal_id INT, status TEXT,
            injected_type TEXT, target_phase TEXT, target_step INT);
    """)
    c.executemany("INSERT INTO goal_attempt VALUES (?,1,?,?,NULL)", rows)
    c.executemany("INSERT INTO failure_injection_log VALUES (?,1,?,?,?,?)", log)
    return c


def _goal(cls):
    # Attempt 1 failed by its injection; attempt 2 was abandoned before t0.
    rows = [(11, 1, "failure"), (12, 2, "failure")]
    log = [(11, "injected", cls, "tool", 2)]
    plan = {"policy": "SR_warm", "cls": cls, "phase": "tool", "ordinal": 2}
    prep = {1: {"validity": []}, 2: {"abandon": True, "validity": []}}
    return _conn(rows, log), plan, prep


def test_abandoned_clean_class_valid():
    conn, plan, prep = _goal("timeout")
    out = rv.classify(conn, 1, plan, prep)
    assert out["codes"] == [] and out["detail"][rv.ABANDONED] == 2


def test_abandoned_persistent_class_valid():
    conn, plan, prep = _goal(rv.PERSIST_PREFIX + "timeout")
    out = rv.classify(conn, 1, plan, prep)
    assert rv.INJECTION_MISMATCH not in out["codes"]
    assert out["detail"][rv.ABANDONED] == 2


def test_not_abandoned_persistent_still_needs_a2_injection():
    conn, plan, prep = _goal(rv.PERSIST_PREFIX + "timeout")
    prep[2] = {"validity": []}
    assert rv.INJECTION_MISMATCH in rv.classify(conn, 1, plan, prep)["codes"]
