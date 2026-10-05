"""
tests/test_invariant_catalog.py (39.5.1 WP 1e)

Plants one violation per invariant in an in memory store and checks that the
catalog finds exactly those, and that a clean store passes.

AUTHOR: Deepak Panigrahy
"""

import sqlite3

from core.integrity.catalog import evaluate, load_catalog

SCHEMA = """
CREATE TABLE runs (run_id INTEGER PRIMARY KEY, baseline_id TEXT,
  pkg_energy_uj INT, baseline_energy_uj INT, dynamic_energy_uj INT, attributed_energy_uj INT,
  planning_energy_uj INT, execution_energy_uj INT, synthesis_energy_uj INT, inter_phase_energy_uj INT,
  cpu_avg_mhz REAL, cpu_busy_mhz REAL, package_temp_celsius REAL, max_temp_c REAL,
  min_temp_c REAL, interrupt_rate REAL, energy_sample_coverage_pct REAL, gpu_total_energy_uj INT);
CREATE TABLE idle_baselines (baseline_id TEXT PRIMARY KEY);
CREATE TABLE goal_execution (goal_id INTEGER PRIMARY KEY, success INT, total_energy_uj INT);
CREATE TABLE goal_attempt (attempt_id INTEGER PRIMARY KEY, goal_id INT, run_id INT, energy_uj INT,
  attempt_number INT DEFAULT 1, started_at_ns INT, finished_at_ns INT);
CREATE TABLE energy_samples_v2 (run_id INT, timestamp_ns INT);
CREATE TABLE thermal_samples (run_id INT, timestamp_ns INT);
CREATE TABLE spans (span_id TEXT, trace_id TEXT, parent_span_id TEXT, run_id INT,
  start_ns INT, end_ns INT);
CREATE TABLE stage_graph (graph_hash TEXT PRIMARY KEY, definition TEXT);
CREATE TABLE stage_event (event_id TEXT, run_uid TEXT, run_id INT, stage_id TEXT,
  scope TEXT, graph_hash TEXT);
"""

# A clean run: pkg 10 J, idle 4 J, dyn 6 J, attr 3 J, phases 2.5 J.
CLEAN = (1, "b1", 10_000_000, 4_000_000, 6_000_000, 3_000_000,
         1_000_000, 1_000_000, 500_000, None, 2000, 1500, 50, 60, 40, 900, 95, None)


def _db(rows, goals, attempts, spans):
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO idle_baselines VALUES ('b1')")
    conn.executemany("INSERT INTO runs VALUES (" + ",".join("?" * 18) + ")", rows)
    # v119 columns arrive after these rows exist, as on a real store, so the
    # rows predate the column and INV-P1 skips them (start_time_ns NULL).
    conn.execute("ALTER TABLE runs ADD COLUMN start_time_ns INT")
    conn.execute("ALTER TABLE runs ADD COLUMN measurement_log_level TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN global_run_id TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN observability_overflow INT")  # 2b column, read by INV-EV5
    conn.execute("ALTER TABLE stage_event ADD COLUMN error_ref TEXT")  # v120 column, read by INV-EV5
    conn.execute("CREATE TABLE migration_history (version INT, type TEXT, source TEXT, "
                 "status TEXT, applied_at TEXT)")
    conn.execute("INSERT INTO migration_history VALUES "
                 "(119, 'schema', 'core', 'applied', '2026-10-03 22:50:49')")
    conn.executemany("INSERT INTO goal_execution VALUES (?,?,?)", goals)
    conn.executemany("INSERT INTO goal_attempt (attempt_id, goal_id, run_id, energy_uj) VALUES (?,?,?,?)",
                     attempts)
    conn.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?)", spans)
    return conn


def _status(conn):
    return {r["id"]: r["status"] for r in evaluate(conn, load_catalog())}


def test_clean_store_passes(monkeypatch, tmp_path):
    # INV-EV5 needs an error directory; without one it is not_evaluable, never pass
    monkeypatch.setenv("ALEMS_ERROR_DIR", str(tmp_path))
    spans = [("s1", "t1", None, 1, 0, 100), ("s2", "t1", "s1", 1, 10, 90)]
    st = _status(_db([CLEAN], [(1, 1, 3_000_000)], [(1, 1, 1, 3_000_000)], spans))
    assert all(v in ("pass", "delegated") for v in st.values()), st


def test_planted_violations_found():
    bad_e2 = (2, "bx", 10_000_000, 4_000_000, 6_000_000, 7_000_000,  # attr > dyn, baseline missing
              None, None, None, None, 2000, 1500, 50, 60, 40, 900, 50, None)
    bad_c1 = (3, "b1", 10_000_000, 4_000_000, 2_000_000, 1_000_000,  # dyn != pkg - idle
              None, None, None, None, 0, 1500, 50, 60, 40, 900, 95, None)  # cpu_avg_mhz 0
    bad_c2 = (4, "b1", 10_000_000, 4_000_000, 6_000_000, None,       # phases of unknown attr
              1_000_000, None, None, None, 2000, 1500, 50, 60, 40, 900, 95, None)
    goals = [(1, 1, None),          # E3: success with NULL energy
             (2, 0, 9)]             # E4: attempts sum 3 J, goal says 9 uJ
    attempts = [(1, 1, 1, None),    # E5: run 1 attributed 3 J, attempt NULL
                (2, 2, 2, 3_000_000)]   # own run (INV-A1); run 2 attributed 7 J, so E5 also fails here
    spans = [("s1", "t1", None, 1, 0, 100), ("s2", "t1", "zz", 1, 10, 90)]  # parent missing
    st = _status(_db([CLEAN, bad_e2, bad_c1, bad_c2], goals, attempts, spans))
    for inv in ("INV-E1", "INV-E2", "INV-C1", "INV-C2", "INV-E3", "INV-E4",
                "INV-E5", "INV-B1", "INV-S1a"):
        assert st[inv] == "fail", (inv, st)
    assert st["INV-M1"] == "warn"


def test_missing_table_is_not_evaluable():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE runs (run_id INTEGER)")
    st = _status(conn)
    assert st["INV-S1a"] == "not_evaluable"
    assert st["INV-D1"] == "delegated"
