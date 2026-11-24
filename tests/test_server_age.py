"""Tests for core/serving/server_age.py (45.2f) on an in memory store."""
import datetime
import sqlite3
import time

from core.serving import server_age as sa


def _store(server_start_ns, run_start_ns):
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE runs(run_id INTEGER, start_time_ns INTEGER);
        CREATE TABLE gpu_samples(run_id, sample_start_ns, interval_ns, util_gpu_pct,
                                 temperature_c, sm_clock_mhz, mem_clock_mhz);
        CREATE TABLE energy_validation_engine_provenance(provenance_id INTEGER PRIMARY KEY,
            exp_id, server_pid, server_start_utc, server_instance_id);""")
    c.execute("INSERT INTO runs VALUES (1, ?)", (run_start_ns,))
    iso = datetime.datetime.fromtimestamp(server_start_ns / 1e9, datetime.timezone.utc)
    c.execute("INSERT INTO energy_validation_engine_provenance VALUES (1, 7, 123, ?, 'abc')",
              (iso.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",))
    return c


def test_fresh_and_warm_differ():
    now = time.time_ns()
    fresh = sa.compute(_store(now - 5 * 10**9, now), 1, 7)
    warm = sa.compute(_store(now - 3600 * 10**9, now), 1, 7)
    assert 4 < fresh["time_since_server_start_s"] < 6
    assert warm["time_since_server_start_s"] > 3500
    assert fresh["time_since_boot_s"] is not None


def test_busy_observed_and_first_sample():
    now = time.time_ns()
    c = _store(now - 100 * 10**9, now)
    c.execute("INSERT INTO gpu_samples VALUES (0, ?, ?, 50, 50, 2400, NULL)", (now - 50 * 10**9, 10**9))
    c.execute("INSERT INTO gpu_samples VALUES (1, ?, ?, 90, 56, 2411, NULL)", (now + 1, 10**8))
    row = sa.compute(c, 1, 7)
    assert row["gpu_busy_observed_since_start_s"] == 0.5
    assert (row["gpu_temp_c"], row["gpu_sm_clock_mhz"], row["gpu_mem_clock_mhz"]) == (56, 2411, None)


def test_no_server_and_no_samples_are_null():
    now = time.time_ns()
    row = sa.compute(_store(now - 10**9, now), 1, 99)
    assert row["time_since_server_start_s"] is None
    assert row["gpu_busy_observed_since_start_s"] is None
    assert row["time_since_driver_load_s"] is None and row["driver_load_source"] == "none"
