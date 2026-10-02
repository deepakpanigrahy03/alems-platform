"""
tests/test_energy_window.py (39.5.1 1d.1)

Window energy on every storage format with synthetic in memory stores, so the
same tests prove Intel and AMD (RAPL legacy), GN100 (SPBM v2) and Apple
Silicon (IOKit v2) on any machine. platform_class is injected; host facts are
never read.
"""

import sqlite3

import pytest

from core.attribution.energy_window import (
    EnergyWindowResolverFactory,
    Sample,
    energy_in_window,
    window_energy_for_run,
)

RUN = 1


def _store():
    """Minimal schema with only the columns the resolvers read."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY, start_time_ns INTEGER,
                           task_duration_ns INTEGER, rapl_at_t0_uj INTEGER);
        CREATE TABLE energy_samples (run_id INTEGER, timestamp_ns INTEGER,
            sample_start_ns INTEGER, sample_end_ns INTEGER,
            pkg_start_uj INTEGER, pkg_end_uj INTEGER);
        CREATE TABLE energy_domains (domain_id INTEGER PRIMARY KEY,
            parent_domain_id INTEGER, is_cumulative INTEGER);
        CREATE TABLE energy_samples_v2 (sample_id INTEGER PRIMARY KEY,
            run_id INTEGER, source_id INTEGER, timestamp_ns INTEGER, interval_ns INTEGER);
        CREATE TABLE energy_sample_domains (sample_id INTEGER, run_id INTEGER,
            domain_id INTEGER, source_id INTEGER, energy_uj REAL);
        INSERT INTO runs VALUES (1, 0, 1000, NULL);
    """)
    return c


def _v2(c, rows, domain):
    """rows: (sample_id, timestamp_ns, interval_ns, energy_uj)."""
    for sid, ts, iv, e in rows:
        c.execute("INSERT INTO energy_samples_v2 VALUES (?,?,?,?,?)", (sid, RUN, 1, ts, iv))
        c.execute("INSERT INTO energy_sample_domains VALUES (?,?,?,?,?)", (sid, RUN, domain, 1, e))


# ---- pure math ---------------------------------------------------------------

def test_overlap_proportional_across_samples():
    s = [Sample(0, 100, 1000), Sample(100, 200, 1000)]
    r = energy_in_window(s, 50, 150)
    assert r.energy_uj == 1000 and r.n_samples == 2 and r.coverage == 1.0


def test_no_overlap_is_none_not_zero():
    r = energy_in_window([Sample(0, 100, 1000)], 200, 300)
    assert r.energy_uj is None and r.n_samples == 0


@pytest.mark.parametrize("a,b", [(100, 100), (200, 100), (None, 100), (0, None)])
def test_empty_or_missing_window_is_none(a, b):
    assert energy_in_window([Sample(0, 1000, 1)], a, b).energy_uj is None


def test_measured_zero_stays_zero():
    r = energy_in_window([Sample(0, 100, 0)], 0, 100)
    assert r.energy_uj == 0 and r.n_samples == 1


def test_partial_coverage_reported():
    r = energy_in_window([Sample(0, 100, 1000)], 50, 250)
    assert r.energy_uj == 500 and r.coverage == pytest.approx(0.25)


# ---- platform formats ----------------------------------------------------------

def test_rapl_legacy_intel_amd():
    c = _store()
    c.executemany("INSERT INTO energy_samples VALUES (?,?,?,?,?,?)", [
        (RUN, 100, 0, 100, 5000, 6000), (RUN, 200, 100, 200, 6000, 7000)])
    r = window_energy_for_run(c.cursor(), RUN, 50, 150, platform_class="intel_x86")
    assert r.energy_uj == 1000
    r = window_energy_for_run(c.cursor(), RUN, 50, 150, platform_class="amd_x86")
    assert r.energy_uj == 1000


def test_spbm_v2_gn100_midpoint():
    c = _store()
    c.execute("INSERT INTO energy_domains VALUES (28, NULL, 1)")
    _v2(c, [(1, 50, 100, 1000.0), (2, 150, 100, 1000.0)], 28)
    r = window_energy_for_run(c.cursor(), RUN, 25, 75, platform_class="nvidia_grace")
    assert r.energy_uj == 500 and r.n_samples == 1


def test_iokit_v2_apple_child_domain():
    c = _store()
    c.executemany("INSERT INTO energy_domains VALUES (?,?,?)", [(10, None, 1), (12, 10, 1)])
    _v2(c, [(1, 50, 100, 800.0)], 12)
    r = window_energy_for_run(c.cursor(), RUN, 0, 50, platform_class="apple_silicon")
    assert r.energy_uj == 400


def test_null_platform_is_none():
    c = _store()
    r = window_energy_for_run(c.cursor(), RUN, 0, 100, platform_class="linux_riscv")
    assert r.energy_uj is None


# ---- prefill (G119) --------------------------------------------------------------

def test_prefill_window_excludes_earlier_calls(monkeypatch):
    from core.attribution.legacy_v1 import ttft_tpot_etl as etl
    monkeypatch.setattr(EnergyWindowResolverFactory, "_load_platform_class",
                        classmethod(lambda cls: "intel_x86"))
    c = _store()
    c.executemany("INSERT INTO energy_samples VALUES (?,?,?,?,?,?)", [
        (RUN, 100, 0, 100, 0, 1000), (RUN, 200, 100, 200, 1000, 2000),
        (RUN, 300, 200, 300, 2000, 3000)])
    # Second call: request at 200, first token at 300; the first call's energy
    # (samples before 200) must not be counted (old code returned 3000).
    assert etl._compute_prefill_energy(c, RUN, 300, 200) == 1000
    assert etl._compute_prefill_energy(c, RUN, 300, None) is None
