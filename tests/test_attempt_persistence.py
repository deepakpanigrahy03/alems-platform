"""
tests/test_attempt_persistence.py (39.5.1 WP 1i, DESIGN_39_5_1_G137_v3)

Proves the A+ persistence contract without hardware:
  * every closed attempt window gets its own run, linked by attempt_id (C3)
  * attempt energy is read back from its own run (C4), never summed (G85 test)
  * a failing raw persist keeps earlier attempts durable (A+4)
  * the exception path persists raw data and re raises the original error

AUTHOR: Deepak Panigrahy
"""

import sqlite3

import pytest

import core.execution.goal_execution_manager as gem


def _conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, attributed_energy_uj INT);
        CREATE TABLE goal_attempt (attempt_id INTEGER PRIMARY KEY, goal_id INT, run_id INT,
                                   attempt_number INT, energy_uj INT, span_id TEXT);
        CREATE TABLE orchestration_events (event_id INTEGER PRIMARY KEY, run_id INT, attempt_id INT);
        INSERT INTO goal_attempt VALUES (11, 1, NULL, 1, NULL, NULL), (12, 1, NULL, 2, NULL, NULL);
    """)
    return c


def _measured():
    # Each fake result carries its own window energy; the run stores exactly that.
    return [
        {"attempt_id": 11, "attempt_num": 1, "outcome": "failure",
         "result": {"e": 31_198_219}, "run_id": None},
        {"attempt_id": 12, "attempt_num": 2, "outcome": "success",
         "result": {"e": 12_637_135}, "run_id": None},
    ]


@pytest.fixture
def fake_persistence(monkeypatch):
    conn = _conn()
    calls = {"derived": []}

    def persist_raw(db, exp_id, hw_id, result, workflow_type, rep_num):
        if result.get("boom"):
            raise RuntimeError("disk full")
        cur = conn.execute("INSERT INTO runs (attributed_energy_uj) VALUES (?)", (result["e"],))
        conn.commit()
        return cur.lastrowid

    def run_derived(db, run_id, result):
        calls["derived"].append(run_id)

    monkeypatch.setattr(gem._rp, "persist_raw", persist_raw)
    monkeypatch.setattr(gem._rp, "run_derived", run_derived)
    monkeypatch.setattr(gem, "_write_attempt_spans", lambda *a, **k: None)
    return conn, calls


def test_each_attempt_own_run(fake_persistence):
    conn, calls = fake_persistence
    m = _measured()
    win, runs, failed = gem._persist_attempts(None, conn, 1, 1, 1, "agentic", 1, {}, m)
    assert failed == [] and len(set(runs)) == 2
    links = dict(conn.execute("SELECT attempt_id, run_id FROM goal_attempt").fetchall())
    assert links[11] != links[12]
    assert win == links[12]
    assert calls["derived"] == runs


def test_no_run_holds_summed_energy(fake_persistence):
    # G85 regression: each run and attempt carries only its own window energy.
    conn, _ = fake_persistence
    gem._persist_attempts(None, conn, 1, 1, 1, "agentic", 1, {}, _measured())
    rows = conn.execute("""SELECT ga.energy_uj, r.attributed_energy_uj
                           FROM goal_attempt ga JOIN runs r ON r.run_id = ga.run_id""").fetchall()
    assert sorted(r[0] for r in rows) == [12_637_135, 31_198_219]
    assert all(r[0] == r[1] for r in rows)
    assert 31_198_219 + 12_637_135 not in [r[1] for r in rows]


def test_partial_raw_failure_keeps_committed(fake_persistence):
    conn, _ = fake_persistence
    m = _measured()
    m[1]["result"]["boom"] = True
    win, runs, failed = gem._persist_attempts(None, conn, 1, 1, 1, "agentic", 1, {}, m)
    assert failed == [12] and len(runs) == 1
    assert conn.execute("SELECT run_id FROM goal_attempt WHERE attempt_id = 11").fetchone()[0] is not None
    assert conn.execute("SELECT run_id FROM goal_attempt WHERE attempt_id = 12").fetchone()[0] is None


def test_exception_path_persists_raw_and_reraises(fake_persistence, monkeypatch):
    conn, calls = fake_persistence

    def impl(*args, **kwargs):
        state = kwargs["_state"]
        state.update(conn=conn, goal_id=1)
        state["measured"].extend(_measured()[:1])   # attempt 1 closed, then the goal crashes
        raise ValueError("retry adapter crashed")

    monkeypatch.setattr(gem, "_execute_goal_impl", impl)
    with pytest.raises(ValueError, match="retry adapter crashed"):
        gem.execute_goal(None, 1, 1, None, None, {}, "agentic", 1, None, None)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert calls["derived"] == []   # exception path: raw only (A+ rule 2)
