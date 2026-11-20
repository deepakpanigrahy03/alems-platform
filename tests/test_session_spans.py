"""
Tests for the recording span vocabulary and the spans the recorder writes.

No hardware, no threads, no store on disk: the recorder's span methods are
driven with a fake clock and an in-memory SQLite store behind a fake writer
with the same interface as the injected writer (open, close, batch,
execute, execute_many, last_row_id).
"""
import contextlib
import sqlite3
import unittest

from alems_sdk.spans import AttrSpec, SpanContractError

from extensions.thermal_exposure.recorder import ExperimentRecorder, recover_open_sessions
from extensions.thermal_exposure.session_spans import SessionSpans, partition_problems
from extensions.thermal_exposure.vocabulary import THERMAL_SESSION

# the columns the span code uses, with the same constraints as the core tables
SCHEMA = """
CREATE TABLE spans (span_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, parent_span_id TEXT,
  run_id INTEGER, vocabulary TEXT NOT NULL, vocabulary_version TEXT NOT NULL,
  kind TEXT NOT NULL, name TEXT NOT NULL, start_ns INTEGER NOT NULL, end_ns INTEGER,
  start_wall TEXT, status TEXT NOT NULL CHECK (status IN ('open','closed','error')),
  tenant_kind TEXT, tenant_ref TEXT, provenance_ref TEXT);
CREATE TABLE span_placements (placement_id INTEGER PRIMARY KEY AUTOINCREMENT,
  span_id TEXT NOT NULL, node TEXT NOT NULL, device TEXT NOT NULL, phase TEXT,
  start_ns INTEGER NOT NULL, end_ns INTEGER);
CREATE TABLE span_attributes (attr_id INTEGER PRIMARY KEY AUTOINCREMENT, span_id TEXT NOT NULL,
  key TEXT NOT NULL, value_text TEXT, value_type TEXT NOT NULL
  CHECK (value_type IN ('string','int','float','bool','json')), UNIQUE (span_id, key));
CREATE TABLE runs (run_id INTEGER PRIMARY KEY, start_time_ns INTEGER);
CREATE TABLE te_session (session_id INTEGER PRIMARY KEY, global_session_id TEXT,
  node_name TEXT, status TEXT, status_reason TEXT, cooldown_status TEXT, start_ns INTEGER);
CREATE TABLE te_thermal_samples (session_id INTEGER, timestamp_ns INTEGER);
"""

S = 1_000_000_000          # one second in ns
BASE = 1_700_000_000 * S   # wall clock at open


class FakeClock:
    """Monotonic time moved by the test; wall = BASE + mono."""
    def __init__(self):
        self.mono = 0

    def wall_ns(self):
        return BASE + self.mono

    def mono_ns(self):
        return self.mono


class FakeWriter:
    """Same calls as the injected writer, on one shared connection."""
    def __init__(self, conn):
        self.conn, self._last = conn, None

    def open(self):
        pass

    def close(self):
        pass

    @contextlib.contextmanager
    def batch(self):
        with self.conn:              # commit, or roll back on an exception
            yield

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self._last = cur.lastrowid
        return [dict(r) for r in cur.fetchall()] if cur.description else []

    def execute_many(self, sql, rows):
        self.conn.executemany(sql, rows)

    def last_row_id(self):
        return self._last


def _store():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _recorder(conn, clock, cooldown=None):
    """A recorder with no readers and no threads, opened by hand."""
    rec = ExperimentRecorder(readers={}, writer_factory=lambda: FakeWriter(conn), rates_hz={},
                             node_name="node-a", machine_profile="test_profile",
                             pre_window_s=30.0, cooldown=cooldown, clock=clock)
    rec._base_wall, rec._base_mono = clock.wall_ns(), clock.mono_ns()
    rec.session_id, rec._gsid = 7, "node-a-1-%d" % BASE
    rec._open_spans()
    return rec


def _run(rec, clock, run_s, then_s):
    """One run of run_s seconds followed by then_s seconds between runs."""
    rec.pause()
    clock.mono += run_s * S
    rec.resume()
    clock.mono += then_s * S


def _finish(rec, clock, status="closed", cooldown_status="not_requested"):
    rec._finish_spans(status, cooldown_status, rec._ts())
    rec._flush_spans_strict()


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM spans")]


def _attrs(conn, kind):
    rows = conn.execute("SELECT a.key, a.value_text FROM span_attributes a "
                        "JOIN spans s ON s.span_id = a.span_id WHERE s.kind = ?", (kind,))
    return {r["key"]: r["value_text"] for r in rows}


class VocabularyTest(unittest.TestCase):
    """The vocabulary refuses everything the contract forbids (spec test 4)."""

    def test_unknown_kind(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_open("lunch", "session")

    def test_wrong_parent(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_open("run_slot", "session")   # belongs in workload
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_open("workload", None)        # only session is a root

    def test_wrong_type(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_close("gap", {"seconds": "ten"})
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_close("workload", {"runs": True})   # bool is not int

    def test_value_not_allowed(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_close("idle_window",
                                           {"purpose": "middle", "planned_s": 1.0, "actual_s": 1.0})

    def test_missing_required(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_close("pause", {"planned_s": 10.0, "actual_s": 10.0})

    def test_unknown_attribute(self):
        with self.assertRaises(SpanContractError):
            THERMAL_SESSION.validate_close("gap", {"seconds": 1.0, "colour": "red"})

    def test_bad_type_name_refused(self):
        with self.assertRaises(SpanContractError):
            AttrSpec("date")

    def test_goal_slot_two_attempts(self):
        """E2C shape (written by the runner later): two attempts in one slot."""
        THERMAL_SESSION.validate_open("goal_slot", "workload")
        THERMAL_SESSION.validate_close("goal_slot", {
            "goal_id": "g1", "workflow": "linear_first", "attempt_run_ids": [11, 12],
            "success": True, "agent_attempt": "run"})
        THERMAL_SESSION.validate_close("goal_slot", {
            "goal_id": "g2", "workflow": "linear_first", "attempt_run_ids": [13],
            "success": True, "agent_attempt": "not_run"})

    def test_same_interface_as_older_vocabularies(self):
        self.assertTrue(THERMAL_SESSION.is_valid_kind("session"))
        self.assertTrue(THERMAL_SESSION.is_valid_parent("session", None))
        self.assertTrue(THERMAL_SESSION.needs_placement("gap"))


class RecorderSpanTest(unittest.TestCase):
    """Spans written by the recorder (spec tests 1 and 3)."""

    def setUp(self):
        self.conn, self.clock = _store(), FakeClock()

    def test_three_runs_partition(self):
        rec = _recorder(self.conn, self.clock)
        self.clock.mono += 30 * S
        for run_s, gap_s in ((60, 5), (90, 7), (45, 2)):
            _run(rec, self.clock, run_s, gap_s)
        _finish(rec, self.clock)
        rows = _rows(self.conn)
        self.assertEqual(partition_problems(rows), [])
        kinds = sorted(r["kind"] for r in rows)
        self.assertEqual(kinds, sorted(["session", "idle_window", "workload", "run_slot",
                                        "run_slot", "run_slot", "gap", "gap", "cooling_tail"]))
        self.assertTrue(all(r["run_id"] is None for r in rows))
        self.assertTrue(all(r["status"] == "closed" for r in rows))

    def test_boundaries_are_pause_and_resume(self):
        rec = _recorder(self.conn, self.clock)
        self.clock.mono += 30 * S
        _run(rec, self.clock, 60, 5)
        _finish(rec, self.clock)
        by_kind = {r["kind"]: r for r in _rows(self.conn)}
        self.assertEqual(by_kind["idle_window"]["end_ns"], BASE + 30 * S)   # first pause
        self.assertEqual(by_kind["run_slot"]["start_ns"], BASE + 30 * S)
        self.assertEqual(by_kind["run_slot"]["end_ns"], BASE + 90 * S)      # resume
        self.assertEqual(by_kind["workload"]["end_ns"], BASE + 90 * S)      # last resume
        self.assertEqual(by_kind["cooling_tail"]["start_ns"], BASE + 90 * S)
        self.assertEqual(by_kind["session"]["end_ns"], BASE + 95 * S)

    def test_no_run_is_all_pre_window(self):
        rec = _recorder(self.conn, self.clock)
        self.clock.mono += 40 * S
        _finish(rec, self.clock)
        rows = _rows(self.conn)
        self.assertEqual(partition_problems(rows), [])
        self.assertEqual(sorted(r["kind"] for r in rows), ["idle_window", "session"])

    def test_every_span_placed_with_logical_node(self):
        rec = _recorder(self.conn, self.clock)
        _run(rec, self.clock, 10, 1)
        _finish(rec, self.clock)
        missing = self.conn.execute(
            "SELECT COUNT(*) FROM spans s LEFT JOIN span_placements p ON p.span_id = s.span_id "
            "WHERE p.span_id IS NULL").fetchone()[0]
        self.assertEqual(missing, 0)
        nodes = {r[0] for r in self.conn.execute("SELECT DISTINCT node FROM span_placements")}
        self.assertEqual(nodes, {"node-a"})
        open_ends = self.conn.execute(
            "SELECT COUNT(*) FROM span_placements WHERE end_ns IS NULL").fetchone()[0]
        self.assertEqual(open_ends, 0)

    def test_run_slot_matched_to_run(self):
        self.conn.execute("INSERT INTO runs VALUES (41, ?)", (BASE + 30 * S + 1000,))
        rec = _recorder(self.conn, self.clock)
        self.clock.mono += 30 * S
        _run(rec, self.clock, 10, 1)
        _run(rec, self.clock, 10, 1)            # no runs row: stays unmatched
        _finish(rec, self.clock)
        rows = self.conn.execute(
            "SELECT s.name, a.key, a.value_text FROM spans s JOIN span_attributes a "
            "ON a.span_id = s.span_id WHERE s.kind = 'run_slot' AND a.key IN ('run_id','run_match')")
        got = {(r["name"], r["key"]): r["value_text"] for r in rows}
        self.assertEqual(got[("run_slot:1", "run_id")], "41")
        self.assertEqual(got[("run_slot:1", "run_match")], "matched")
        self.assertEqual(got[("run_slot:2", "run_match")], "none")
        self.assertNotIn(("run_slot:2", "run_id"), got)     # NULL is never written as a value

    def test_cooldown_attributes(self):
        rec = _recorder(self.conn, self.clock, cooldown={"max_s": 600, "tolerance_c": 2.0})
        _run(rec, self.clock, 10, 120)
        _finish(rec, self.clock, cooldown_status="completed")
        attrs = _attrs(self.conn, "cooling_tail")
        self.assertEqual(attrs["status"], "completed")
        self.assertEqual(attrs["reached"], "true")
        self.assertEqual(float(attrs["tolerance_c"]), 2.0)

    def test_span_checks_pass(self):
        rec = _recorder(self.conn, self.clock)
        _run(rec, self.clock, 10, 1)
        _finish(rec, self.clock)
        checks = {c[1]: c[2] for c in rec._span_checks()}
        self.assertEqual(checks, {"spans_partition": "pass", "spans_run_match": "warn"})

    def test_aborted_recording_marks_session_error(self):
        rec = _recorder(self.conn, self.clock)
        _run(rec, self.clock, 10, 1)
        _finish(rec, self.clock, status="aborted", cooldown_status="aborted")
        status = self.conn.execute("SELECT status FROM spans WHERE kind = 'session'").fetchone()[0]
        self.assertEqual(status, "error")
        self.assertEqual(partition_problems(_rows(self.conn)), [])

    def test_spans_not_written_during_a_run(self):
        """Only the open-time spans exist until the flush after the run."""
        rec = _recorder(self.conn, self.clock)
        rec.pause()
        self.clock.mono += 10 * S
        self.assertEqual(len(_rows(self.conn)), 2)            # session + pre-window
        rec.resume()
        rec._flush_spans_strict()
        self.assertEqual(len(_rows(self.conn)), 4)            # + workload + run slot

    def test_failed_write_keeps_spans(self):
        rec = _recorder(self.conn, self.clock)
        _run(rec, self.clock, 10, 1)
        good = rec.writer_factory
        rec.writer_factory = lambda: (_ for _ in ()).throw(RuntimeError("store busy"))
        self.assertFalse(rec._flush_spans())
        rec.writer_factory = good
        self.assertTrue(rec._flush_spans())
        self.assertEqual(len(_rows(self.conn)), 4)

    def test_pause_never_raises_on_span_error(self):
        rec = _recorder(self.conn, self.clock)
        rec._span_ids["pre"] = "missing"         # force a span error at the first pause
        rec.pause()                              # must not raise
        rec.resume()


class RecoveryTest(unittest.TestCase):
    """Recovery closes open spans with the aborted recording, idempotently."""

    def test_recovery_closes_spans_once(self):
        conn, clock = _store(), FakeClock()
        rec = _recorder(conn, clock)
        clock.mono += 30 * S
        _run(rec, clock, 10, 1)
        rec._flush_spans_strict()                 # spans left open: the process "dies"
        dead = "node-a-999999999-%d" % BASE       # pid that cannot exist
        conn.execute("UPDATE spans SET trace_id = ?", (dead,))
        conn.execute("INSERT INTO te_session VALUES (7, ?, 'node-a', 'open', NULL, NULL, ?)",
                     (dead, BASE))
        conn.execute("INSERT INTO te_thermal_samples VALUES (7, ?)", (BASE + 45 * S,))
        conn.commit()
        factory = lambda: FakeWriter(conn)
        self.assertEqual(recover_open_sessions(factory, "node-a"), 1)
        rows = _rows(conn)
        self.assertTrue(all(r["status"] != "open" for r in rows))
        slot = [r for r in rows if r["kind"] == "run_slot"][0]
        self.assertEqual(slot["end_ns"], BASE + 40 * S)      # known end kept
        session = [r for r in rows if r["kind"] == "session"][0]
        self.assertEqual(session["end_ns"], BASE + 45 * S)   # last sample
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM span_placements "
                                      "WHERE end_ns IS NULL").fetchone()[0], 0)
        before = [tuple(r.values()) for r in rows]
        self.assertEqual(recover_open_sessions(factory, "node-a"), 0)
        self.assertEqual([tuple(r.values()) for r in _rows(conn)], before)


class SessionSpansTest(unittest.TestCase):
    """Tree rules that do not need the recorder."""

    def test_end_before_start_refused(self):
        sp = SessionSpans(THERMAL_SESSION, "t", "n")
        sid = sp.open("session", "s", 100)
        with self.assertRaises(SpanContractError):
            sp.close(sid, 50, {})

    def test_closed_span_immutable(self):
        sp = SessionSpans(THERMAL_SESSION, "t", "n")
        sid = sp.open("session", "s", 0)
        wl = sp.open("workload", "w", 0, sid)
        sp.close(wl, 10, {"runs": 1})
        with self.assertRaises(SpanContractError):
            sp.close(wl, 20, {"runs": 2})

    def test_partition_detects_gap_and_overlap(self):
        def r(i, kind, parent, s, e):
            return {"span_id": i, "parent_span_id": parent, "kind": kind,
                    "start_ns": s, "end_ns": e, "status": "closed"}
        base = [r("s", "session", None, 0, 100), r("i", "idle_window", "s", 0, 30)]
        gap = base + [r("w", "workload", "s", 31, 100), r("x", "run_slot", "w", 31, 100)]
        self.assertTrue(any("gap" in p for p in partition_problems(gap)))
        over = base + [r("w", "workload", "s", 29, 100), r("x", "run_slot", "w", 29, 100)]
        self.assertTrue(any("overlap" in p for p in partition_problems(over)))


if __name__ == "__main__":
    unittest.main()
