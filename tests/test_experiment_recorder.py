"""
Unit tests for the continuous experiment recorder and the run hook.

Runs against an in-memory SQLite store built from the real thermal_exposure
extension migrations (e001, e002), with fake readers, so no hardware and no
real store are touched.
"""

import contextlib
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from core.recording import hook
from extensions.thermal_exposure import recorder as recmod

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "migrations" / "extensions" / "thermal_exposure"

# core tables the extension tables and view refer to (stubs, FKs not enforced)
STUBS = """
CREATE TABLE runs (run_id INTEGER PRIMARY KEY, exp_id INTEGER, start_time_ns INTEGER);
CREATE TABLE thermal_zones (zone_id INTEGER PRIMARY KEY);
CREATE TABLE cooling_devices (device_id INTEGER PRIMARY KEY);
CREATE TABLE thermal_samples_v2 (run_id INTEGER, zone_id INTEGER, timestamp_ns INTEGER,
    temp_celsius REAL, quality_flag TEXT);
"""


def _split(sql):
    """Split a migration into statements the same way the migrate tool does."""
    out, cur = [], []
    for line in sql.splitlines(keepends=True):
        cur.append(line)
        if sqlite3.complete_statement("".join(cur)):
            s = "".join(cur).strip()
            if s and not all(l.strip().startswith("--") or not l.strip()
                             for l in s.splitlines()):
                out.append(s)
            cur = []
    return out


class FakeWriter:
    """Writer double over one shared in-memory connection (thread safe)."""

    lock = threading.RLock()

    def __init__(self, conn, fail=None):
        self.conn, self.fail = conn, fail

    def open(self):
        if self.fail and self.fail():
            raise RuntimeError("store locked")

    def close(self):
        pass

    @contextlib.contextmanager
    def batch(self):
        with self.lock:
            yield
            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params or ())
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def execute_many(self, sql, rows):
        with self.lock:
            self.conn.executemany(sql, list(rows))
            return len(rows)

    def last_row_id(self):
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture
def store():
    """In-memory store with e001 + e002 applied."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(STUBS)
    for f in ("e001_create_thermal_exposure.sql", "e002_recording_between_runs.sql"):
        for stmt in _split((MIG / f).read_text()):
            conn.execute(stmt)
    conn.commit()
    return conn


def _zones():
    """Fake thermal reader: two zones, VALID, with the reader's own timestamp."""
    return [{"zone_id": 1, "timestamp_ns": 1, "temp_celsius": 40.0,
             "quality_flag": "VALID", "invalid_reason": None},
            {"zone_id": 2, "timestamp_ns": 1, "temp_celsius": 41.0,
             "quality_flag": "VALID", "invalid_reason": None}]


def _rec(store, readers=None, clock=None, fail=None):
    """Recorder with fast rates and no pre-window."""
    return recmod.ExperimentRecorder(
        readers=readers or {"THERMAL_ZONE": _zones}, writer_factory=lambda: FakeWriter(store, fail),
        rates_hz={"THERMAL_ZONE": 50.0}, pre_window_s=0.0, anchor_s=0.1, flush_s=0.05,
        clock=clock, offset_fn=lambda: (None, None))


def test_no_samples_stored_during_a_run(store):
    rec = _rec(store)
    rec.open()
    time.sleep(0.3)
    rec.pause()
    time.sleep(0.3)
    rec.resume()
    time.sleep(0.3)
    rec.close()
    start, end = rec._workload_start, rec._workload_end
    ts = [r[0] for r in store.execute("SELECT timestamp_ns FROM te_thermal_samples")]
    assert ts, "between-runs samples were written"
    assert not [t for t in ts if start <= t < end], "a sample was stored inside the run"
    row = store.execute("SELECT status, run_id, workload_start_ns, workload_end_ns "
                        "FROM te_session").fetchone()
    assert row == ("closed", None, start, end)


class SteppingClock(recmod.SystemClock):
    """Wall clock jumps forward 5 s after 0.25 s; monotonic is real."""

    def __init__(self):
        self.t0 = time.monotonic_ns()

    def wall_ns(self):
        jump = 5_000_000_000 if time.monotonic_ns() - self.t0 > 250_000_000 else 0
        return time.time_ns() + jump


def test_wall_clock_step_does_not_move_sample_times(store):
    rec = _rec(store, clock=SteppingClock())
    rec.open()
    time.sleep(0.6)
    rec.close()
    ts = [r[0] for r in store.execute(
        "SELECT timestamp_ns FROM te_thermal_samples WHERE zone_id = 1 ORDER BY sample_id")]
    assert all(b > a for a, b in zip(ts, ts[1:])), "time line went backwards"
    assert max(b - a for a, b in zip(ts, ts[1:])) < 1_000_000_000, "time line jumped"
    assert store.execute("SELECT MAX(stepped) FROM te_clock_anchor").fetchone()[0] == 1
    res = store.execute("SELECT result FROM te_session_check "
                        "WHERE check_name = 'clock_step'").fetchone()[0]
    assert res == "fail"          # 5 s step is larger than one interval


def test_reader_failure_writes_read_failed_and_continues(store):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if 5 <= calls["n"] < 10:
            raise OSError("sysfs read error")
        return _zones()
    rec = _rec(store, readers={"THERMAL_ZONE": flaky})
    rec.open()
    time.sleep(0.5)
    rec.close()
    failed = store.execute("SELECT COUNT(*), MAX(temp_celsius) FROM te_thermal_samples "
                           "WHERE quality_flag = 'READ_FAILED'").fetchone()
    assert failed[0] >= 2 and failed[1] is None   # NULL, never 0
    late = store.execute("SELECT COUNT(*) FROM te_thermal_samples WHERE quality_flag = "
                         "'VALID' AND sample_id > (SELECT MAX(sample_id) FROM "
                         "te_thermal_samples WHERE quality_flag = 'READ_FAILED')").fetchone()[0]
    assert late > 0, "recording did not continue after the failure"


def test_recovery_marks_aborted_once_and_keeps_samples(store):
    store.execute("INSERT INTO te_session (global_session_id, node_name, profile_id, "
                  "profile_sha256, pacing_plugin, machine_profile, clock_basis, status, "
                  "start_ns) VALUES ('local-999999999-1', 'local', 'p', 'x', 'none', 'm', "
                  "'c', 'open', 1)")
    store.execute("INSERT INTO te_thermal_samples (session_id, zone_id, timestamp_ns, "
                  "temp_celsius, quality_flag) VALUES (1, 1, 2, 40.0, 'VALID')")
    store.commit()
    wf = lambda: FakeWriter(store)
    assert recmod.recover_open_sessions(wf, "local") == 1
    assert recmod.recover_open_sessions(wf, "local") == 0      # idempotent
    row = store.execute("SELECT status, cooldown_status FROM te_session").fetchone()
    assert row == ("aborted", "aborted")
    assert store.execute("SELECT COUNT(*) FROM te_thermal_samples").fetchone()[0] == 1


def test_flush_failure_keeps_rows(store):
    state = {"fail": True}
    rec = _rec(store)
    rec.open()                                  # session row written while healthy
    rec.writer_factory = lambda: FakeWriter(store, fail=lambda: state["fail"])
    time.sleep(0.3)
    kept = sum(len(v) for v in rec._buffers.values())
    assert kept > 0, "rows were dropped on a failed write"
    state["fail"] = False
    rec.close()
    n = store.execute("SELECT COUNT(*) FROM te_thermal_samples").fetchone()[0]
    assert n >= kept


def test_hook_is_noop_without_recording_and_never_raises():
    hook.clear_active()
    hook.notify_run_start()                     # no recorder: nothing happens
    hook.notify_run_end()

    class Boom:
        def pause(self):
            raise RuntimeError("x")

        def resume(self):
            raise RuntimeError("y")
    b = Boom()
    hook.set_active(b)
    try:
        hook.notify_run_start()                 # must not raise
        hook.notify_run_end()
    finally:
        hook.clear_active(b)
    assert hook.get_active() is None


def test_no_reads_and_no_writes_while_paused(store):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return _zones()
    rec = _rec(store, readers={"THERMAL_ZONE": counting})
    rec.open()
    time.sleep(0.2)
    rec.pause()
    time.sleep(0.1)                      # let an in-flight tick finish
    reads_at_pause = calls["n"]
    anchors_at_pause = store.execute("SELECT COUNT(*) FROM te_clock_anchor").fetchone()[0]
    time.sleep(0.5)                      # a 'run': nothing may be read or written
    assert calls["n"] == reads_at_pause, "a sensor was read during a run"
    assert store.execute("SELECT COUNT(*) FROM te_clock_anchor").fetchone()[0] == anchors_at_pause
    rec.resume()
    time.sleep(0.2)
    assert calls["n"] > reads_at_pause, "recording did not resume"
    rec.close()


def test_bad_rows_never_block_a_source(store):
    def odd():
        # VALID without a value, and UNAVAILABLE on a thermal zone: both refused by
        # the table as given; the recorder must normalise them instead of stalling
        return [{"zone_id": 1, "temp_celsius": None, "quality_flag": "VALID"},
                {"zone_id": 2, "temp_celsius": None, "quality_flag": "UNAVAILABLE"}]
    rec = _rec(store, readers={"THERMAL_ZONE": odd})
    rec.open()
    time.sleep(0.3)
    rec.close()
    flags = {r[0] for r in store.execute("SELECT DISTINCT quality_flag FROM te_thermal_samples")}
    assert flags == {"MISSING"}
    assert sum(len(v) for v in rec._buffers.values()) == 0, "rows stuck in the buffer"


def test_malformed_reader_output_does_not_kill_the_source(store):
    calls = {"n": 0}

    def sometimes_broken():
        calls["n"] += 1
        if calls["n"] == 3:
            return [{"no_zone_id_here": True}]          # KeyError inside the tick
        return _zones()
    rec = _rec(store, readers={"THERMAL_ZONE": sometimes_broken})
    rec.open()
    time.sleep(0.4)
    rec.close()
    assert calls["n"] > 5, "the source thread stopped after the malformed row"


def test_read_in_flight_at_pause_is_not_counted_as_a_gap(store):
    gate = threading.Event()

    def slow():
        gate.wait(0.5)                                  # hold one read across the pause
        return _zones()
    rec = _rec(store, readers={"THERMAL_ZONE": slow})
    rec.open()
    time.sleep(0.05)
    rec.pause()                                         # a read is in flight
    gate.set()
    time.sleep(1.0)                                     # a long 'run'
    gate.clear()
    rec.resume()
    gate.set()                                          # fast reads after the run
    time.sleep(0.3)
    rec.close()
    st = rec._stats["THERMAL_ZONE"]
    assert st.max_gap_ns < 900_000_000, "the run was counted as a recorder gap"


def test_close_while_paused_resumes_and_records_reason(store):
    rec = _rec(store)
    rec.open()
    time.sleep(0.2)
    rec.pause()                                         # run raised: no end notification
    rec.close()
    row = store.execute("SELECT status, status_reason FROM te_session").fetchone()
    assert row[0] == "closed" and "still open" in row[1]
