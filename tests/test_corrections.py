"""
tests/test_corrections.py (39.5.1 1d.3)

G90 (impossible zeros to NULL) and G119 (prefill recomputed with the window
rule) on a synthetic store: dry run writes nothing, apply is exact, a second
apply changes nothing.
"""

import sqlite3

from core.validation import corrections as c


def _store(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, start_time_ns INTEGER,
            task_duration_ns INTEGER, rapl_at_t0_uj INTEGER,
            cpu_busy_mhz REAL, cpu_avg_mhz REAL, package_temp_celsius REAL,
            max_temp_c REAL, min_temp_c REAL, interrupt_rate REAL);
        CREATE TABLE energy_samples (run_id INTEGER, timestamp_ns INTEGER,
            sample_start_ns INTEGER, sample_end_ns INTEGER,
            pkg_start_uj INTEGER, pkg_end_uj INTEGER);
        CREATE TABLE energy_domains (domain_id INTEGER PRIMARY KEY,
            parent_domain_id INTEGER, is_cumulative INTEGER);
        CREATE TABLE energy_samples_v2 (sample_id INTEGER PRIMARY KEY, run_id INTEGER,
            source_id INTEGER, timestamp_ns INTEGER, interval_ns INTEGER);
        CREATE TABLE energy_sample_domains (sample_id INTEGER, run_id INTEGER,
            domain_id INTEGER, source_id INTEGER, energy_uj REAL);
        CREATE TABLE llm_interactions (interaction_id INTEGER PRIMARY KEY, run_id INTEGER,
            streaming_enabled INTEGER, request_start_ns INTEGER,
            first_token_time_ns INTEGER, prefill_energy_uj INTEGER);
        INSERT INTO runs VALUES (1, 0, 300, NULL, 0, 2400, 0, 55, 0, 0);
        INSERT INTO energy_samples VALUES
            (1, 100, 0, 100, 0, 1000), (1, 200, 100, 200, 1000, 2000),
            (1, 300, 200, 300, 2000, 3000);
        -- old ETL: whole run up to first token (3000) and a 0 without timestamps
        INSERT INTO llm_interactions VALUES (10, 1, 1, 200, 300, 3000), (11, 1, 1, NULL, 300, 0);
    """)
    conn.commit()
    return conn


def test_dry_run_writes_nothing(tmp_path):
    db = str(tmp_path / "s.db")
    _store(db).close()
    assert c.run(db, ["G90", "G119"], False, "intel_x86") == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT cpu_busy_mhz FROM runs").fetchone()[0] == 0
    assert conn.execute("SELECT prefill_energy_uj FROM llm_interactions WHERE interaction_id=10").fetchone()[0] == 3000


def test_apply_exact_then_idempotent(tmp_path):
    db = str(tmp_path / "s.db")
    _store(db).close()
    assert c.run(db, ["G90", "G119"], True, "intel_x86") == 0
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT cpu_busy_mhz, cpu_avg_mhz, package_temp_celsius, max_temp_c, "
                       "min_temp_c, interrupt_rate FROM runs").fetchone()
    assert row == (None, 2400, None, 55, None, None)  # measured values untouched
    pf = dict(conn.execute("SELECT interaction_id, prefill_energy_uj FROM llm_interactions"))
    assert pf == {10: 1000, 11: None}
    assert c.apply_g90(conn) == 0
    assert c.apply_g119(conn, "intel_x86") == 0
    assert list(tmp_path.glob("s.db.pre_corrections_*.bak"))
