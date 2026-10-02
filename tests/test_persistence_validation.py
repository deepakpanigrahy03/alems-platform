"""
Tests for core.validation.persistence: INV-D1, INV-D2, undeclared tables,
coverage, and the write site lint (unit and whole repository).

AUTHOR: Deepak Panigrahy
"""
import os
import sqlite3

import pytest

from core.validation import persistence as P

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DECL_YAML = """
version: 1
tables:
  runs: {link: run, key: unique_index}
  goal_attempt: {link: run, key: unique_index}
  cpu_samples: {link: run, key: [run_id, timestamp_ns]}
  hallucination_events: {link: attempt, key: [attempt_id, hallucination_type]}
  audit_log: {link: run, key: null, reason: append only}
  absent_ext: {link: run, key: [run_id]}
writers: [core/database/]
exceptions: {core/legacy.py: G96}
"""


@pytest.fixture
def decl(tmp_path):
    """Declarations for the fixture store."""
    p = tmp_path / "keys.yaml"
    p.write_text(DECL_YAML)
    return P.load_declarations(str(p))


def _store(tmp_path, extra_sql=""):
    """Small store: runs 1 and 2, one attempt each, clean samples."""
    db = str(tmp_path / "s.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, global_run_id TEXT);
        CREATE TABLE goal_attempt (attempt_id INTEGER PRIMARY KEY, goal_id INT, run_id INT);
        CREATE TABLE cpu_samples (sample_id INTEGER PRIMARY KEY, run_id INT, timestamp_ns INT);
        CREATE TABLE hallucination_events (hallucination_id INTEGER PRIMARY KEY, attempt_id INT, hallucination_type TEXT);
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY, run_id INT);
        CREATE TABLE catalog (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO runs (run_id) VALUES (1), (2);
        INSERT INTO goal_attempt VALUES (10, 100, 1), (20, 200, 2);
        INSERT INTO cpu_samples (run_id, timestamp_ns) VALUES (1, 5), (1, 6), (2, 5);
        INSERT INTO hallucination_events (attempt_id, hallucination_type) VALUES (10, 'x');
    """ + extra_sql)
    con.commit()
    con.close()
    return db


def test_clean_store_passes(tmp_path, decl):
    """No findings; classification lists are filled."""
    r = P.validate(_store(tmp_path), decl=decl)
    assert r.ok, r.findings
    assert "cpu_samples" in r.evaluated and "runs" in r.by_construction
    assert r.not_evaluable == {"audit_log": "append only"} and r.absent == ["absent_ext"]


def test_d1_duplicate_detected_and_scoped(tmp_path, decl):
    """Duplicate sample of run 1 is found store wide and for run 1, not for run 2."""
    db = _store(tmp_path, "INSERT INTO cpu_samples (run_id, timestamp_ns) VALUES (1, 5);")
    f = [x for x in P.validate(db, decl=decl).findings if x.check == "INV-D1"]
    assert len(f) == 1 and f[0].table == "cpu_samples" and f[0].count == 1
    assert P.validate(db, runs=[2], decl=decl).ok


def test_d2_orphan_null_and_indirect(tmp_path, decl):
    """Dangling run id, NULL run id and an attempt of a missing run are orphans."""
    db = _store(tmp_path, """
        INSERT INTO cpu_samples (run_id, timestamp_ns) VALUES (9, 1), (NULL, 2);
        INSERT INTO hallucination_events (attempt_id, hallucination_type) VALUES (99, 'y');
    """)
    f = {(x.check, x.table): x.count for x in P.validate(db, decl=decl).findings}
    assert f[("INV-D2", "cpu_samples")] == 2
    assert f[("INV-D2", "hallucination_events")] == 1


def test_undeclared_run_table_fails(tmp_path, decl):
    """A new table with a run reference must be declared before it passes."""
    db = _store(tmp_path, "CREATE TABLE new_feature (id INTEGER PRIMARY KEY, run_id INT);")
    f = [x for x in P.validate(db, decl=decl).findings if x.check == "UNDECLARED"]
    assert [x.table for x in f] == ["new_feature"]


def test_global_run_id_is_not_a_link(tmp_path, decl):
    """global_run_id alone does not make a table run linked."""
    db = _store(tmp_path, "CREATE TABLE x (id INTEGER PRIMARY KEY, global_run_id TEXT);")
    assert P.validate(db, decl=decl).ok


def test_coverage_lost_and_ok(tmp_path, decl):
    """Run 2 has no hallucination rows while run 1 has one: LOST."""
    rows = {r.table: r.status for r in P.coverage(_store(tmp_path), [1], [2], decl=decl)}
    assert rows["hallucination_events"] == "LOST" and rows["cpu_samples"] == "OK"


def test_missing_store_refused(tmp_path, decl):
    """An empty path or a missing file raises instead of passing silently."""
    for path in ("", str(tmp_path / "nope.db")):
        with pytest.raises(FileNotFoundError):
            P.validate(path, decl=decl)


def test_store_without_runs_fails(tmp_path, decl):
    """A database that is not an A-LEMS store is a CONFIG finding."""
    db = str(tmp_path / "e.db")
    sqlite3.connect(db).close()
    r = P.validate(db, decl=decl)
    assert not r.ok and r.findings[0].check == "CONFIG"


def test_null_key_needs_reason(tmp_path):
    """The declaration file cannot hide a table behind key null."""
    p = tmp_path / "k.yaml"
    p.write_text("tables: {t: {link: run, key: null}}")
    with pytest.raises(ValueError):
        P.load_declarations(str(p))


def test_lint_unit(tmp_path, decl):
    """Writes outside writers and exceptions are reported; allowed ones are not."""
    for rel, body in [("core/bad.py", 'q = "INSERT INTO cpu_samples (a) VALUES (1)"\n'),
                      ("core/database/ok.py", 'q = "INSERT INTO cpu_samples (a) VALUES (1)"\n'),
                      ("core/legacy.py", 'q = "UPDATE cpu_samples SET a=1"\n'),
                      ("core/prose.py", "# update cpu_samples later\n")]:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    assert P.lint_write_sites(str(tmp_path), decl=decl) == [("core/bad.py", 1, "cpu_samples")]


def test_repository_has_no_undeclared_write_sites():
    """Engine guard: every run linked write is in a writer module or a gap listed exception."""
    hits = P.lint_write_sites(REPO)
    assert hits == [], "\n".join("%s:%d %s" % h for h in hits)


def test_declarations_file_parses():
    """The shipped persistence_keys.yaml is valid."""
    d = P.load_declarations()
    assert "runs" in d.tables and d.tables["goal_execution"].run_columns == ("first_run_id", "winning_run_id")
