"""
core/sequencer/store.py: all sequencer writes, each in its own short transaction.

Writes go through the DatabaseManager handle (db.transaction(), db.db.conn),
never a new sqlite3 connection (EEI-2, WCC-2). Any store error propagates as
SequencerStoreError: the sequencer stops dispatch on it, whatever the plan
says about treatment failures.
"""

import json
import time
from typing import Any, Dict, List, Optional

_TABLES = ("energy_validation_seq_sessions", "energy_validation_seq_steps",
           "energy_validation_seq_attempts", "energy_validation_seq_attempt_runs",
           "energy_validation_seq_step_results", "energy_validation_seq_bracket_eligibility")


class SequencerStoreError(RuntimeError):
    """A sequencer write failed; no further step may be dispatched."""


class SequenceStore:
    """Sequencer tables of one store."""

    def __init__(self, db):
        # type: (Any) -> None
        self.db = db
        self.conn = db.db.conn

    def check_schema(self):
        # type: () -> None
        """All sequencer tables exist and foreign keys are enforced on this connection."""
        missing = [t for t in _TABLES if not self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
        if missing:
            raise SequencerStoreError("sequencer tables missing (run: alems dev migrate --run): %s"
                                      % ", ".join(missing))
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SequencerStoreError("foreign keys cannot be enforced on the writer connection")

    def _tx(self, fn, *args):
        """Run fn in one transaction; any error becomes SequencerStoreError."""
        try:
            with self.db.transaction():
                return fn(*args)
        except SequencerStoreError:
            raise
        except Exception as exc:
            raise SequencerStoreError("%s: %s" % (fn.__name__, exc))

    def create_session(self, plan, plan_sha, order, versions):
        # type: (Dict[str, Any], str, List[List[str]], Dict[str, Any]) -> int
        """Session row (status running) with the resolved plan and its hash."""
        def _ins():
            cur = self.conn.execute(
                "INSERT INTO energy_validation_seq_sessions (name, plan_path, plan_source_sha256, "
                "resolved_plan_json, resolved_plan_sha256, plan_schema_version, seed, "
                "realized_order_json, versions_json, status, start_utc_ns) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
                (plan.get("name"), plan.get("path"), plan.get("source_sha256"),
                 json.dumps(plan, sort_keys=True, default=str), plan_sha, plan["schema_version"],
                 plan.get("seed"), json.dumps(order), json.dumps(versions, default=str), time.time_ns()))
            return cur.lastrowid
        return self._tx(_ins)

    def insert_steps(self, session_id, steps):
        # type: (int, List[Dict[str, Any]]) -> List[int]
        """Materialise every planned step before dispatch (never started work stays visible)."""
        def _ins():
            ids = []
            for s in steps:
                cur = self.conn.execute(
                    "INSERT INTO energy_validation_seq_steps (session_id, cycle_id, bracket_id, condition, "
                    "role, kind, params_json, planned_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, s["cycle_id"], s["bracket_id"], s["condition"], s["role"], s["kind"],
                     json.dumps(s["params"], sort_keys=True, default=str), s["planned_index"]))
                ids.append(cur.lastrowid)
            return ids
        return self._tx(_ins)

    def begin_attempt(self, step_id, settle_start, settle_end, gate=None):
        # type: (int, int, int, Optional[Dict[str, Any]]) -> int
        """Commit a running attempt (with its readiness gate record) before dispatch."""
        def _ins():
            n = self.conn.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM "
                                  "energy_validation_seq_attempts WHERE step_id = ?", (step_id,)).fetchone()[0]
            cur = self.conn.execute(
                "INSERT INTO energy_validation_seq_attempts (step_id, attempt_no, execution_status, "
                "settle_start_mono_ns, settle_end_mono_ns, gate_json, exec_start_mono_ns, exec_start_utc_ns) "
                "VALUES (?, ?, 'running', ?, ?, ?, ?, ?)",
                (step_id, n, settle_start, settle_end, json.dumps(gate, default=str) if gate else None,
                 time.monotonic_ns(), time.time_ns()))
            return cur.lastrowid
        return self._tx(_ins)

    def skip_step(self, step_id, reason_code, detail, gate=None):
        # type: (int, str, str, Optional[Dict[str, Any]]) -> None
        """Record a step skipped before execution (no measurement window)."""
        self._tx(lambda: self.conn.execute(
            "INSERT INTO energy_validation_seq_attempts (step_id, attempt_no, execution_status, "
            "reason_code, reason_detail, gate_json) SELECT ?, COALESCE(MAX(attempt_no), 0) + 1, "
            "'skipped', ?, ?, ? FROM energy_validation_seq_attempts WHERE step_id = ?",
            (step_id, reason_code, detail, json.dumps(gate, default=str) if gate else None, step_id)))

    def finish_attempt(self, attempt_id, status, outcome, extra_fn=None):
        # type: (int, str, Dict[str, Any], Any) -> bool
        """
        Conditional terminal transition (only from running). extra_fn(conn) runs
        in the same transaction (idle baseline insert). Returns False when the
        attempt was no longer running (another resume closed it).
        """
        def _upd():
            if extra_fn is not None:
                extra_fn(self.conn)
            cur = self.conn.execute(
                "UPDATE energy_validation_seq_attempts SET execution_status = ?, reason_code = ?, "
                "reason_detail = ?, exec_end_mono_ns = ?, exec_end_utc_ns = ?, meas_start_utc_ns = ?, "
                "meas_end_utc_ns = ?, baseline_id = ?, exp_id = ? "
                "WHERE attempt_id = ? AND execution_status = 'running'",
                (status, outcome.get("reason_code"), outcome.get("reason_detail"), time.monotonic_ns(),
                 time.time_ns(), outcome.get("meas_start_utc_ns"), outcome.get("meas_end_utc_ns"),
                 outcome.get("baseline_id"), outcome.get("exp_id"), attempt_id))
            for key, (value, unit) in (outcome.get("results") or {}).items():
                self.conn.execute("INSERT OR REPLACE INTO energy_validation_seq_step_results "
                                  "(attempt_id, key, value, unit) VALUES (?, ?, ?, ?)",
                                  (attempt_id, key, None if value is None else str(value), unit))
            return cur.rowcount == 1
        return self._tx(_upd)

    def linked_runs(self, attempt_id):
        # type: (int) -> List[tuple]
        """(run_id, start_time_ns, end_time_ns) of runs linked to an attempt."""
        return self.conn.execute(
            "SELECT r.run_id, r.start_time_ns, r.end_time_ns FROM energy_validation_seq_attempt_runs a "
            "JOIN runs r ON r.run_id = a.run_id WHERE a.attempt_id = ? ORDER BY a.child_seq",
            (attempt_id,)).fetchall()

    def end_session(self, session_id, status, reason=None):
        # type: (int, str, Optional[str]) -> None
        self._tx(lambda: self.conn.execute(
            "UPDATE energy_validation_seq_sessions SET status = ?, stop_reason = ?, end_utc_ns = ? "
            "WHERE session_id = ?", (status, reason, time.time_ns(), session_id)))

    def running_attempts(self, session_id):
        # type: (int) -> List[int]
        return [r[0] for r in self.conn.execute(
            "SELECT a.attempt_id FROM energy_validation_seq_attempts a JOIN energy_validation_seq_steps s "
            "USING(step_id) WHERE s.session_id = ? AND a.execution_status = 'running'", (session_id,))]

    def pending_steps(self, session_id):
        # type: (int) -> List[Dict[str, Any]]
        """Steps of a session with no completed, failed or skipped attempt, in planned order."""
        rows = self.conn.execute(
            "SELECT step_id, cycle_id, bracket_id, condition, role, kind, params_json, planned_index "
            "FROM energy_validation_seq_steps s WHERE session_id = ? AND NOT EXISTS (SELECT 1 FROM "
            "energy_validation_seq_attempts a WHERE a.step_id = s.step_id AND a.execution_status IN "
            "('completed', 'failed', 'skipped')) ORDER BY planned_index", (session_id,)).fetchall()
        keys = ("step_id", "cycle_id", "bracket_id", "condition", "role", "kind", "params", "planned_index")
        return [dict(zip(keys, r[:6] + (json.loads(r[6]), r[7]))) for r in rows]
