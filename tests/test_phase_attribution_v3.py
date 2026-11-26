"""Phase attribution v3 acceptance (claude/DESIGN_45_phase_attribution.md section 3)."""
import sqlite3

import pytest

from core.attribution.legacy_v1 import phase_attribution_etl as p

S = 1_000_000_000  # 1 s in ns


def _db(proc_a, proc_b, dyn_a_j, dyn_b_j, attributed_j, ticks=True, cpu_fraction=None):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE energy_domains (domain_id INTEGER, name TEXT);
        CREATE TABLE energy_sample_domains (sample_id INTEGER, run_id INTEGER, domain_id INTEGER, energy_uj REAL);
        CREATE TABLE energy_samples (run_id INTEGER, sample_start_ns INTEGER, sample_end_ns INTEGER,
                                     pkg_start_uj INTEGER, pkg_end_uj INTEGER);
        CREATE TABLE interrupt_samples (run_id INTEGER, sample_start_ns INTEGER, sample_end_ns INTEGER,
            user_ticks_start INTEGER, user_ticks_end INTEGER, system_ticks_start INTEGER, system_ticks_end INTEGER,
            nice_ticks_start INTEGER, nice_ticks_end INTEGER, proc_ticks_start INTEGER, proc_ticks_end INTEGER);
        CREATE TABLE orchestration_events (event_id INTEGER, run_id INTEGER, phase TEXT,
            start_time_ns INTEGER, end_time_ns INTEGER, raw_energy_uj INTEGER, cpu_fraction_per_phase REAL,
            attributed_energy_uj INTEGER, attribution_method TEXT, quality_score REAL);
        CREATE TABLE runs (run_id INTEGER, attributed_energy_uj INTEGER, baseline_energy_uj INTEGER,
            duration_ns INTEGER, cpu_fraction REAL, start_time_ns INTEGER, pre_task_duration_ns INTEGER,
            task_duration_ns INTEGER, planning_energy_uj INTEGER, execution_energy_uj INTEGER,
            synthesis_energy_uj INTEGER, inter_phase_energy_uj INTEGER);
    """)
    # planning [0,1s), execution [1s,2s): adjacent, no gap; window [0, 2s]; baseline 0
    c.execute("INSERT INTO runs VALUES (1, ?, 0, ?, ?, 0, 0, ?, NULL, NULL, NULL, NULL)",
              (attributed_j * 10**6, 2 * S, cpu_fraction, 2 * S))
    c.execute("INSERT INTO orchestration_events VALUES (1, 1, 'planning', 0, ?, NULL, NULL, NULL, NULL, NULL)", (S,))
    c.execute("INSERT INTO orchestration_events VALUES (2, 1, 'execution', ?, ?, NULL, NULL, NULL, NULL, NULL)", (S, 2 * S))
    c.execute("INSERT INTO energy_samples VALUES (1, 0, ?, 0, ?)", (S, dyn_a_j * 10**6))
    c.execute("INSERT INTO energy_samples VALUES (1, ?, ?, 0, ?)", (S, 2 * S, dyn_b_j * 10**6))
    if ticks:
        c.execute("INSERT INTO interrupt_samples VALUES (1, 0, ?, 0, 100, 0, 0, 0, 0, 0, ?)", (S, proc_a))
        c.execute("INSERT INTO interrupt_samples VALUES (1, ?, ?, 100, 200, 0, 0, 0, 0, ?, ?)",
                  (S, 2 * S, proc_a, proc_a + proc_b))
    else:
        c.execute("INSERT INTO interrupt_samples VALUES (1, 0, ?, 0, 100, 0, 0, 0, 0, NULL, NULL)", (S,))
        c.execute("INSERT INTO interrupt_samples VALUES (1, ?, ?, 100, 200, 0, 0, 0, 0, NULL, NULL)", (S, 2 * S))
    return c


def test_no_gap_example_gpt():
    c = _db(10, 90, 90, 10, 50)
    r = p.compute_phase_attribution(1, conn=c)
    assert r["planning"] == 9 * 10**6 and r["execution"] == 9 * 10**6
    assert r["inter_phase_uj"] == 0 and r["inter_phase_ns"] == 0
    assert r["residual_uj"] == 32 * 10**6
    assert r["n_direct"] == 2 and r["n_fallback"] == 0


def test_measured_zero_share_is_zero():
    c = _db(0, 0, 90, 10, 0)
    r = p.compute_phase_attribution(1, conn=c)
    assert r["planning"] == 0 and r["execution"] == 0 and r["n_direct"] == 2


def test_missing_ticks_use_run_share_inferred():
    c = _db(0, 0, 90, 10, 50, ticks=False, cpu_fraction=0.5)
    r = p.compute_phase_attribution(1, conn=c)
    assert r["planning"] == 45 * 10**6 and r["execution"] == 5 * 10**6
    assert r["n_fallback"] == 2
    m = {row[0] for row in c.execute("SELECT attribution_method FROM orchestration_events")}
    assert m == {"run_share_v3"}


def test_unknown_is_null():
    c = _db(0, 0, 90, 10, 50, ticks=False, cpu_fraction=None)
    r = p.compute_phase_attribution(1, conn=c)
    assert r["planning"] is None and r["execution"] is None and r["residual_uj"] is None


def test_gap_gets_inter_phase():
    assert p._gaps((0, 10), [(2, 4), (3, 6), (8, 9)]) == [(0, 2), (6, 8), (9, 10)]
