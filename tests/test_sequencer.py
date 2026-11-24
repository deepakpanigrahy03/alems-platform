"""Tests for the step sequencer core (45.4c1): plan, crash safe links, attempts, fail rules."""
import contextlib
import os
import sqlite3

import pytest

from core.sequencer import plan as plan_mod
from core.sequencer.context import SequencerLinkError, attempt_scope, link_run
from core.sequencer.runner import Sequencer
from core.sequencer.store import SequenceStore, SequencerStoreError

E005 = os.environ.get("ALEMS_TEST_E005",
                      "migrations/extensions/energy_validation/e005_create_sequencer.sql")


class _Adapter:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)


class _DB:
    """Minimal DatabaseManager: autocommit connection, explicit BEGIN per transaction."""

    def __init__(self, with_e005=True):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("CREATE TABLE runs(run_id INTEGER PRIMARY KEY, exp_id INTEGER,"
                                " start_time_ns INTEGER, end_time_ns INTEGER);"
                                "CREATE TABLE idle_baselines(baseline_id TEXT PRIMARY KEY);")
        if with_e005:
            self.conn.executescript(open(E005).read())
        self.db = _Adapter(self.conn)

    @contextlib.contextmanager
    def transaction(self):
        self.conn.execute("BEGIN")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def insert_baseline(self, row):
        self.conn.execute("INSERT OR IGNORE INTO idle_baselines VALUES (?)", (row["baseline_id"],))


def _plan(seed=7, cycles=2):
    doc = {"sequence": {"seed": seed, "cycles": cycles, "settle_s": 0, "baseline": {"kind": "idle", "duration_s": 0}},
           "blocks": [{"condition": "A", "treatment": {"kind": "profile", "profile": "a.yaml"}},
                      {"condition": "B", "treatment": {"kind": "profile", "profile": "b.yaml"}}]}
    return plan_mod.resolve(doc, ("idle", "profile"))


def _persist_child(db, run_id, wf, rep, fail_link=False):
    """Stand in for persist_raw: run row and link in one transaction."""
    with db.transaction():
        db.conn.execute("INSERT INTO runs VALUES (?, 1, 1, 2)", (run_id,))
        if fail_link:
            db.conn.execute("DROP TABLE energy_validation_seq_attempt_runs")
        link_run(db, run_id, wf, rep)


def _attempt(db):
    st = SequenceStore(db)
    st.check_schema()
    p = _plan()
    sid = st.create_session(p, "h", [], {})
    steps = plan_mod.materialize(p)
    ids = st.insert_steps(sid, steps)
    return st, sid, st.begin_attempt(ids[1], 0, 0)


def test_plan_seed_reproducible_and_brackets():
    a, b = plan_mod.materialize(_plan(seed=3)), plan_mod.materialize(_plan(seed=3))
    assert [s["condition"] for s in a] == [s["condition"] for s in b]
    assert [s["role"] for s in a[:3]] == ["pre", "treatment", "post"] and len(a) == 12


def test_unknown_kind_rejected_before_run():
    with pytest.raises(plan_mod.PlanError):
        plan_mod.resolve({"blocks": [{"condition": "X", "treatment": {"kind": "copyrate"}}]}, ("idle",))


def test_kill_after_first_child_keeps_link():
    db = _DB()
    st, sid, aid = _attempt(db)
    with attempt_scope(aid):
        _persist_child(db, 1, "linear", 0)
    # process "dies" here: second child never exists; resume closes the attempt
    st.finish_attempt(aid, "interrupted", {"reason_code": "resumed"})
    assert db.conn.execute("SELECT attempt_id, run_id FROM energy_validation_seq_attempt_runs").fetchall() == [(aid, 1)]
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert st.begin_attempt(db.conn.execute("SELECT step_id FROM energy_validation_seq_attempts "
                                            "WHERE attempt_id=?", (aid,)).fetchone()[0], 0, 0) != aid


def test_link_failure_rolls_back_run():
    db = _DB()
    st, sid, aid = _attempt(db)
    with attempt_scope(aid), pytest.raises(SequencerLinkError):
        _persist_child(db, 1, "linear", 0, fail_link=True)
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_no_attempt_ordinary_run_untouched():
    db = _DB(with_e005=False)
    with db.transaction():
        db.conn.execute("INSERT INTO runs VALUES (5, 1, 1, 2)")
        link_run(db, 5, "linear", 0)
    assert db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_conditional_finish_only_once():
    db = _DB()
    st, sid, aid = _attempt(db)
    assert st.finish_attempt(aid, "interrupted", {}) is True
    assert st.finish_attempt(aid, "failed", {}) is False


def test_link_to_finished_attempt_refused():
    db = _DB()
    st, sid, aid = _attempt(db)
    st.finish_attempt(aid, "completed", {})
    with attempt_scope(aid), pytest.raises(SequencerLinkError):
        _persist_child(db, 2, "agentic", 0)


def test_pre_failure_skips_treatment_and_post(monkeypatch):
    db = _DB()
    from core.sequencer import kinds
    monkeypatch.setitem(kinds.KINDS, "idle", lambda s, c: {"status": "failed", "reason_code": "no_direct_energy"})
    monkeypatch.setitem(kinds.KINDS, "profile", lambda s, c: pytest.fail("treatment must be skipped"))
    seq = Sequencer(db, config_loader=None)
    assert seq.start(_plan(cycles=1)) == 0
    rows = db.conn.execute("SELECT s.role, a.execution_status, a.reason_code FROM energy_validation_seq_attempts a "
                           "JOIN energy_validation_seq_steps s USING(step_id) ORDER BY s.planned_index").fetchall()
    assert rows[1] == ("treatment", "skipped", "pre_baseline_failed") and rows[2][1] == "skipped"


def test_missing_tables_stop_before_dispatch():
    db = _DB(with_e005=False)
    with pytest.raises(SequencerStoreError):
        Sequencer(db, None).start(_plan())


def test_link_failure_inside_step_stops_dispatch(monkeypatch):
    db = _DB()
    from core.sequencer import kinds

    def _profile(step, ctx):
        try:
            with attempt_scope(ctx["attempt_id"]):
                _persist_child(db, 9, "linear", 0, fail_link=True)
        except SequencerLinkError:
            pass   # a runner that logs and continues
        return {"status": "completed"}

    monkeypatch.setitem(kinds.KINDS, "idle", lambda s, c: {"status": "completed"})
    monkeypatch.setitem(kinds.KINDS, "profile", _profile)
    assert Sequencer(db, None).start(_plan(cycles=1)) == 1
    assert db.conn.execute("SELECT status FROM energy_validation_seq_sessions").fetchone()[0] == "stopped"
    assert db.conn.execute("SELECT COUNT(*) FROM energy_validation_seq_attempts").fetchone()[0] == 2


def test_counterbalanced_orders():
    two = plan_mod.cycle_orders([{"condition": "A"}, {"condition": "B"}], 4, 17)
    assert [[b["condition"] for b in o] for o in two][:2] in ([["A", "B"], ["B", "A"]], [["B", "A"], ["A", "B"]])
    three = plan_mod.cycle_orders([{"condition": c} for c in "ABC"], 3, 1)
    for pos in range(3):
        assert sorted(o[pos]["condition"] for o in three) == ["A", "B", "C"]


def test_gate_waits_then_passes_and_records():
    from core.sequencer import gates
    temps = iter([80.0, 70.0, 55.0])
    t = {"now": 0.0}
    rec = gates.wait_ready(lambda: {"soc": next(temps), "other": 30.0},
                           {"max_temp_c": 60.0, "sensor_pattern": "soc", "max_wait_s": 100.0, "poll_s": 5.0},
                           sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])
    assert rec["outcome"] == "passed" and rec["first"] == ("soc", 80.0) and rec["last"] == ("soc", 55.0)


def test_gate_off_timeout_and_no_sensor():
    from core.sequencer import gates
    assert gates.wait_ready(lambda: {}, {"max_temp_c": None})["outcome"] == "off"
    t = {"now": 0.0}
    rec = gates.wait_ready(lambda: {"soc": 90.0}, {"max_temp_c": 60.0, "sensor_pattern": ".*",
                           "max_wait_s": 10.0, "poll_s": 5.0},
                           sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"])
    assert rec["outcome"] == "timeout"
    assert gates.wait_ready(lambda: {}, {"max_temp_c": 60.0, "sensor_pattern": ".*",
                                         "max_wait_s": 1.0, "poll_s": 1.0})["outcome"] == "not_available"


def test_plugin_kind_validation_and_skip(monkeypatch):
    from alems_sdk.sequencer import Availability, StepKindABC
    from core.sequencer import kinds

    class Copy(StepKindABC):
        KIND = "copy_rate"
        PARAMS = {"size_mib": {"type": "number", "required": True},
                  "direction": {"type": "string", "choices": ["h2d", "d2h"], "required": True}}

        def is_available(self, step, ctx):
            return Availability(False, "no_gpu", "no device")

        def measure(self, step, ctx):
            return {}

    monkeypatch.setattr(kinds, "_PLUGINS", {"copy_rate": Copy})
    doc = {"blocks": [{"condition": "c", "treatment": {"kind": "copy_rate", "size_mib": 16, "direction": "x"}}]}
    p = plan_mod.resolve(doc, tuple(kinds.all_kinds()))
    assert any("direction" in e for e in kinds.validate_plan(p))
    out = kinds.all_kinds()["copy_rate"]({"params": {"size_mib": 16, "direction": "h2d"}, "bracket_id": "c0:c",
                                          "condition": "c"}, {"attempt_id": 1})
    assert out["status"] == "skipped" and out["reason_code"] == "no_gpu"
