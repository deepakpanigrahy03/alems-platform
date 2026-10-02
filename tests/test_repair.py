"""
Tests for core.validation.repair and core.validation.derived_diff.

AUTHOR: Deepak Panigrahy
"""
import shutil
import sqlite3

import pytest

from core.validation import derived_diff as D
from core.validation import persistence as P
from core.validation import repair as R

KEYS = """
version: 1
tables:
  runs: {link: run, key: unique_index}
  energy_samples_v2: {link: run, key: [run_id, source_id, timestamp_ns]}
  energy_sample_domains: {link: run, key: [run_id, sample_id, domain_id]}
  orchestration_events: {link: run, key: [run_id, phase, start_time_ns]}
  tool_failure_events: {link: run, key: [run_id, orchestration_event_id]}
children:
  energy_samples_v2:
    - {table: energy_sample_domains, column: sample_id, action: delete}
  orchestration_events:
    - {table: tool_failure_events, column: orchestration_event_id, action: repoint}
"""


@pytest.fixture
def setup(tmp_path):
    """Store with one identical duplicate, one conflicting key match, and children."""
    k = tmp_path / "k.yaml"
    k.write_text(KEYS)
    db = str(tmp_path / "s.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, x REAL);
        CREATE TABLE energy_samples_v2 (sample_id INTEGER PRIMARY KEY, run_id INT, source_id INT, timestamp_ns INT);
        CREATE TABLE energy_sample_domains (sample_id INT, run_id INT, domain_id INT, energy_uj INT);
        CREATE TABLE orchestration_events (event_id INTEGER PRIMARY KEY, run_id INT, phase TEXT, start_time_ns INT, e INT);
        CREATE TABLE tool_failure_events (failure_id INTEGER PRIMARY KEY, run_id INT, orchestration_event_id INT);
        INSERT INTO runs VALUES (1, 1.0), (2, 2.0);
        INSERT INTO energy_samples_v2 VALUES (10, 1, 1, 100), (11, 1, 1, 100), (12, 2, 1, 100);
        INSERT INTO energy_sample_domains VALUES (10, 1, 7, 5), (11, 1, 7, 5), (12, 2, 7, 5);
        INSERT INTO orchestration_events VALUES (20, 1, 'p', 1, 9), (21, 1, 'p', 1, 9), (22, 1, 'q', 2, 3), (23, 1, 'q', 2, 4);
        INSERT INTO tool_failure_events VALUES (30, 1, 21);
    """)
    con.commit()
    con.close()
    return db, P.load_declarations(str(k))


def test_plan_identical_only(setup):
    """Identical duplicates are removable; a key match with other content is a conflict."""
    db, decl = setup
    plan = R.plan_dedupe(db, ["energy_samples_v2", "orchestration_events"], decl=decl)
    s = {t: (n, c) for t, n, c, _r in plan.summary()}
    assert s["energy_samples_v2"] == (1, 0)
    assert s["orchestration_events"] == (1, 1)


def test_apply_children_and_invariants(setup, tmp_path):
    """After apply: duplicates gone, child deleted or repointed, D1 and D2 clean for these tables."""
    db, decl = setup
    plan = R.plan_dedupe(db, ["energy_samples_v2", "orchestration_events"], decl=decl)
    con = sqlite3.connect(db)
    done = R.apply_plan(con, plan, decl=decl)
    con.commit()
    assert done["energy_sample_domains(delete)"] == 1 and done["tool_failure_events(repoint)"] == 1
    assert con.execute("SELECT orchestration_event_id FROM tool_failure_events").fetchone()[0] == 20
    assert con.execute("SELECT COUNT(*) FROM energy_sample_domains").fetchone()[0] == 2
    con.close()
    f = [x for x in P.validate(db, decl=decl).findings if x.table == "energy_samples_v2"]
    assert f == []


def test_diff_isolates_change(setup, tmp_path):
    """A copy with one changed derived value shows exactly that column and run."""
    db, _decl = setup
    b = str(tmp_path / "b.db")
    shutil.copy(db, b)
    con = sqlite3.connect(b)
    con.execute("UPDATE runs SET x = 4.0 WHERE run_id = 2")
    con.commit()
    con.close()
    cols, counts = D.diff_stores(db, b, [1, 2], profiles={"runs": {"key": ["run_id"]}})
    assert [(c.column, c.rows_changed, c.max_abs) for c in cols] == [("x", 1, 2.0)] and counts == []


def test_merge_keeps_late_written_values(tmp_path):
    """A late column filled only on the duplicate is merged into the kept row."""
    k = tmp_path / "k.yaml"
    k.write_text("""
tables:
  runs: {link: run, key: unique_index}
  llm_interactions: {link: run, key: [run_id, step_index], repair_ignore: [created_at, prefill_energy_uj]}
""")
    decl = P.load_declarations(str(k))
    db = str(tmp_path / "m.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY);
        CREATE TABLE llm_interactions (interaction_id INTEGER PRIMARY KEY, run_id INT, step_index INT,
                                       prompt TEXT, created_at TEXT, prefill_energy_uj INT);
        INSERT INTO runs VALUES (1);
        INSERT INTO llm_interactions VALUES (1, 1, 0, 'p', 't1', NULL), (2, 1, 0, 'p', 't2', 77);
    """)
    con.commit()
    plan = R.plan_dedupe(db, ["llm_interactions"], decl=decl)
    assert plan.summary()[0][1:3] == (1, 0)
    R.apply_plan(con, plan, decl=decl)
    con.commit()
    assert con.execute("SELECT interaction_id, created_at, prefill_energy_uj FROM llm_interactions").fetchall() == [(1, "t1", 77)]
