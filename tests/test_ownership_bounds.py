"""Unit tests for scripts/ownership_bounds.py on synthetic intervals and an in memory store."""
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ob", ROOT / "scripts" / "ownership_bounds.py")
ob = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ob)

MS = 1000000


def test_merge_union():
    assert ob.merge([(5, 8), (0, 3), (2, 4), (9, 9)]) == [(0, 4), (5, 8)]


def test_bounds_example():
    # wait window 100..170 ms; increments 4 J over 50..150 and 5 J over 150..250
    incs = [((50 * MS, 150 * MS), 4.0), ((150 * MS, 250 * MS), 5.0)]
    b = ob.bounds(incs, [(100 * MS, 170 * MS)])
    assert b["lower"] == 0.0 and b["upper"] == 9.0
    assert abs(b["prop"] - (4 * 0.5 + 5 * 0.2)) < 1e-9
    assert b["n_straddling"] == 2


def test_inside_increment_is_owned():
    incs = [((110 * MS, 160 * MS), 3.0)]
    b = ob.bounds(incs, [(100 * MS, 170 * MS)])
    assert b["lower"] == b["upper"] == b["prop"] == 3.0


def test_widen_moves_support_back():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE energy_samples_v2 (sample_id INTEGER, run_id INTEGER, timestamp_ns INTEGER, interval_ns INTEGER);
        CREATE TABLE energy_sample_domains (sample_id INTEGER, domain_id INTEGER, energy_uj INTEGER);
        INSERT INTO energy_samples_v2 VALUES (1, 7, 200000000, 100000000);
        INSERT INTO energy_sample_domains VALUES (1, 1, 2000000);
        INSERT INTO energy_samples_v2 VALUES (2, 7, 300000000, NULL);
        INSERT INTO energy_sample_domains VALUES (2, 1, NULL);
    """)
    incs = ob.increments(conn, 7, 1, 100 * MS)
    assert incs == [((0, 200 * MS), 2.0)]       # NULL energy and NULL interval skipped


def test_phase_row_notes():
    cfg = {"min_sample_cover": 0.5}
    row = ob.phase_row(1, "agentic", "waiting", "PACKAGE", [(0, 10)], [], None, cfg)
    assert row["note"] == "no samples" and "upper_j" not in row
    incs = [((1000, 2000), 1.0)]
    row = ob.phase_row(1, "agentic", "waiting", "PACKAGE", [(0, 10)], incs, (1000, 2000), cfg)
    assert "clock basis" in row["note"]


def test_summary_ratio_of_sums():
    rows = [{"phase": "waiting", "domain": "GPU", "phase_s": 1.0, "lower_j": 1.0, "prop_j": 2.0, "upper_j": 4.0},
            {"phase": "waiting", "domain": "GPU", "phase_s": 1.0, "lower_j": 3.0, "prop_j": 4.0, "upper_j": 4.0},
            {"phase": "waiting", "domain": "GPU", "upper_j": None}]
    s = ob.summarize(rows)[0]
    assert s["n_runs"] == 2 and s["width_j"] == 4.0 and s["width_share"] == 0.5


def test_dcgm_and_tool_call_windows():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE gpu_samples (run_id INTEGER, sample_start_ns INTEGER, sample_end_ns INTEGER, energy_uj INTEGER);
        INSERT INTO gpu_samples VALUES (3, 100, 200, 5000000), (3, 200, 300, NULL);
        CREATE TABLE orchestration_events (run_id INTEGER, phase TEXT, event_type TEXT, start_time_ns INTEGER, end_time_ns INTEGER);
        INSERT INTO orchestration_events VALUES (3, 'execution', 'tool_call', 10, 20), (3, 'execution', 'llm_call', 30, 40);
    """)
    assert ob.dcgm_increments(conn, 3, 50) == [((50, 200), 5.0)]
    w = ob.phase_windows(conn, 3)
    assert w["tool_call"] == [(10, 20)] and len(w["execution"]) == 2
