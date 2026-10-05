"""
Stage event recording for the v1 execution paths (C-EV, master 5.1).

Design choices (lead decisions, WP 2c):
1. One terminal row per applicable stage per run (pending and running live
   only in memory and the C-LIVE stream). This is the literal form of
   "exactly one lifecycle record" and avoids update traffic.
2. The context manager never swallows: it records failed and re raises.
   The pre existing try/except around the call site decides propagation,
   so control flow is byte for byte the original (design 15.7).
3. Nothing is written inside [t0, t1]: terminal events go to the gate
   (event_seq, live stream, non lossy buffer); rows are persisted by
   persist() through the writer after insert_run, with run_id attached.
4. finalize() closes every applicable stage that was never reached, so
   the run always ends with a complete, terminal stage set.
"""
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Callable, Dict, Iterator, List, Optional

from core.observability import gate
from core.observability import stage_graphs as sg

TERMINAL = ("succeeded", "failed", "skipped", "skipped_dependency")
_BLOCKING = ("failed", "skipped", "skipped_dependency")


class StageContractError(Exception):
    """Raised only in tests or by misuse; never raised into a run path."""


def _next_seq():
    # type: () -> Optional[int]
    """Shared per process counter from the 2b gate (master 5.1 rule 4)."""
    try:
        return gate.next_seq()
    except Exception:  # observability never breaks the run (5.2a)
        return None


def _submit(record):
    # type: (Dict[str, object]) -> None
    """Hand a terminal event to the gate (buffered inside the window)."""
    try:
        gate.submit("event", record)
    except Exception:
        pass  # non lossy overflow is flagged by the gate itself


class StageRecorder(object):
    """Collects stage events for one run, keyed by run_uid."""

    def __init__(self, graph_id, run_uid=None, sandbox_id=None):
        # type: (str, Optional[str], Optional[str]) -> None
        """Bind to a declared graph; run_uid is created here when absent."""
        if graph_id not in sg.GRAPHS:
            raise StageContractError("unknown graph %s" % graph_id)
        self.graph_id = graph_id
        self.graph_hash = sg.graph_hash(graph_id)
        self.run_uid = run_uid or str(uuid.uuid4())
        self.run_id = None  # type: Optional[int]
        self.sandbox_id = sandbox_id
        self.events = {}  # type: Dict[str, Dict[str, object]]
        # Stage ids recorded inside an open transaction scope (None when no scope).
        self._tx = None  # type: Optional[List[str]]
        # Stage ids already written to the store (two phase writing, 3c).
        self._written = set()  # type: set

    def attach_run_id(self, run_id):
        # type: (int) -> None
        """Attach run_id once insert_run returned (EEI-4)."""
        self.run_id = run_id

    def _blocker(self, stage_id):
        # type: (str) -> Optional[str]
        """First required predecessor whose terminal status blocks."""
        for dep in sg.requires_of(self.graph_id, stage_id):
            ev = self.events.get(dep)
            # A not_reached stage is an instrumentation gap, not a failure,
            # so it never blocks a dependent stage.
            if ev is not None and ev["status"] in _BLOCKING and ev.get("reason") != "not_reached":
                return dep
        return None

    def _record(self, stage_id, status, start_ns, reason=None, outcome=None,
                counts=None, blocked_by=None, error_ref=None):
        """
        Record or merge the terminal state of a stage.

        A stage may have several parts (etl_hardware runs before and after
        etl_phase). Parts merge into one row: counts add up, end_ns moves,
        and the status is the worst of the parts (failed beats succeeded).
        Inside a transaction scope the record is held, not submitted, so a
        rollback can still downgrade it.
        """
        nd = sg.node(self.graph_id, stage_id)
        if nd is None:
            return  # non applicable stages emit nothing (design 15.3)
        prev = self.events.get(stage_id)
        if prev is not None:
            self._merge(prev, status, reason, counts)
            return
        rec = {
            "event_id": str(uuid.uuid4()), "run_uid": self.run_uid,
            "sandbox_id": self.sandbox_id, "stage_id": stage_id,
            "stage_version": sg.STAGE_VERSION, "status": status,
            "outcome": outcome, "reason": reason, "event_seq": _next_seq(),
            "pid": os.getpid(), "start_ns": start_ns,
            "end_ns": time.monotonic_ns(), "counts": dict(counts or {}),
            "error_ref": error_ref, "parent_stage_id": None,
            "graph_hash": self.graph_hash, "blocked_by": blocked_by,
            "scope": nd["scope"],
        }
        self.events[stage_id] = rec
        if self._tx is not None:
            self._tx.append(stage_id)  # submitted when the transaction ends
        else:
            _submit(rec)

    def _merge(self, prev, status, reason, counts):
        """Merge a later part of a multi part stage into its row."""
        prev["end_ns"] = time.monotonic_ns()
        for k, v in (counts or {}).items():
            prev["counts"][k] = prev["counts"].get(k, 0) + v
        if status == "failed" and prev["status"] != "failed":
            prev["status"] = "failed"
            prev["outcome"] = None
            prev["reason"] = reason

    @contextmanager
    def tx_scope(self):
        # type: () -> Iterator[None]
        """
        Wrap a writer transaction. Stages recorded inside are held; if the
        transaction raises, every held succeeded stage becomes failed with
        reason rolled_back, because its writes did not survive. Held records
        are submitted when the scope ends. The exception always propagates.
        """
        self._tx = []
        try:
            yield
        except BaseException:
            for sid in self._tx:
                ev = self.events[sid]
                if ev["status"] == "succeeded":
                    ev["status"], ev["outcome"], ev["reason"] = "failed", None, "rolled_back"
            raise
        finally:
            held, self._tx = self._tx, None
            for sid in held:
                _submit(self.events[sid])

    @contextmanager
    def stage(self, stage_id):
        # type: (str) -> Iterator[Dict[str, object]]
        """
        Wrap an existing block. Yields a dict the call site may fill:
        counts (table -> rows), outcome ('unavailable', 'partial'), reason.
        Exceptions are recorded as failed and always re raised.
        """
        start = time.monotonic_ns()
        info = {"counts": {}, "outcome": None, "reason": None}  # type: Dict[str, object]
        dep = self._blocker(stage_id)
        if dep is not None:
            # v1 code ran despite a failed dependency; record what happened
            info["reason"] = "ran_despite:%s" % dep
        # mark this stage active so swallowed failures inside it are noted (2d 7.2)
        token = _obs_errors().push_stage(self, stage_id)
        try:
            yield info
        except BaseException as exc:
            # exception path: capture, code in reason, error_ref on the row (2d 7.1)
            self.__dict__.get("_notes", {}).pop(stage_id, None)
            ref, reason = _stage_failure(exc, info["reason"])
            self._record(stage_id, "failed", start, reason=reason,
                         counts=info["counts"], error_ref=ref)
            raise
        finally:
            _obs_errors().pop_stage(token)
        self._close_ok(stage_id, start, info)

    def note(self, stage_id, error_id, code):
        # type: (str, object, object) -> None
        """
        Record a failure swallowed inside a running stage (2d design 7.2).

        Called by core.observability.errors.capture; error_id is None when
        capture itself failed, which the reason then reports.
        """
        notes = self.__dict__.setdefault("_notes", {})
        notes.setdefault(stage_id, []).append((error_id, code or "unclassified"))

    def _close_noted(self, stage_id, start, info, nd, notes):
        """Swallowed failures: outcome partial if declared, otherwise failed (2d 7.2 to 7.4)."""
        ref = next((e for e, _ in notes if e), None)  # first allocated error_id
        reason = "swallowed:" + ",".join(c for _, c in notes)
        if any(e is None for e, _ in notes):
            reason += ";capture_failed"
        if info["reason"]:
            reason = "%s;%s" % (info["reason"], reason)
        status, outcome = ("succeeded", "partial") if nd.get("partial_ok") else ("failed", None)
        self._record(stage_id, status, start, reason=reason, outcome=outcome,
                     counts=info["counts"] or {}, error_ref=ref)

    def _close_ok(self, stage_id, start, info):
        """Apply the declared stage contract to a normal exit (design 14.5, 2d 7.2)."""
        nd = sg.node(self.graph_id, stage_id) or {}
        counts = info["counts"] or {}
        outcome = info["outcome"]
        if outcome == "unavailable" and not nd.get("unavailable_ok"):
            self._record(stage_id, "failed", start, reason="undeclared_unavailable",
                         counts=counts)
            return
        notes = self.__dict__.get("_notes", {}).pop(stage_id, None)
        if notes:
            self._close_noted(stage_id, start, info, nd, notes)
            return
        if outcome is None and counts and sum(counts.values()) == 0:
            if not nd.get("empty_ok"):
                self._record(stage_id, "failed", start, reason="unexpected_zero_rows",
                             counts=counts)
                return
            outcome = "empty"
        self._record(stage_id, "succeeded", start, reason=info["reason"],
                     outcome=outcome, counts=counts)

    def skip(self, stage_id, reason):
        # type: (str, str) -> None
        """Applicable but intentionally not executed (design 16.3)."""
        self._record(stage_id, "skipped", time.monotonic_ns(), reason=reason)

    def fail(self, stage_id, reason, start_ns=None):
        # type: (str, str, Optional[int]) -> None
        """Record a failure observed without an exception (swallowed paths)."""
        self._record(stage_id, "failed", start_ns or time.monotonic_ns(), reason=reason)

    def finalize(self):
        # type: () -> None
        """Close unreached applicable stages in topological order."""
        for sid in sg.topological_order(self.graph_id):
            if sid in self.events:
                continue
            dep = self._blocker(sid)
            if dep is not None:
                self._record(sid, "skipped_dependency", time.monotonic_ns(),
                             reason="dependency_%s" % self.events[dep]["status"],
                             blocked_by=dep)
            else:
                self._record(sid, "skipped", time.monotonic_ns(), reason="not_reached")

    def rows(self):
        # type: () -> List[tuple]
        """Rows not yet written, in event_seq order, for the stage_event insert."""
        now = datetime.now(timezone.utc).isoformat()
        evs = sorted((e for e in self.events.values() if e["stage_id"] not in self._written),
                     key=lambda e: (e["event_seq"] is None, e["event_seq"] or 0))
        return [(e["event_id"], e["run_uid"], self.run_id, e["sandbox_id"],
                 e["stage_id"], e["stage_version"], e["status"], e["outcome"],
                 e["reason"], e["event_seq"], e["pid"], e["start_ns"], e["end_ns"],
                 json.dumps(e["counts"], sort_keys=True), e["error_ref"],
                 e["parent_stage_id"], e["graph_hash"], e["blocked_by"], e["scope"],
                 now) for e in evs]


INSERT_GRAPH = ("INSERT INTO stage_graph (graph_hash, graph_id, graph_version, "
                "definition, created_at) SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM stage_graph WHERE graph_hash = ?)")

INSERT_EVENT = ("INSERT INTO stage_event (event_id, run_uid, run_id, sandbox_id, "
                "stage_id, stage_version, status, outcome, reason, event_seq, pid, "
                "start_ns, end_ns, counts, error_ref, parent_stage_id, graph_hash, "
                "blocked_by, scope, created_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def persist(recorder, execute_many, final=True):
    # type: (StageRecorder, Callable[[str, List[tuple]], None], bool) -> int
    """
    Write the graph (idempotent) and all stage rows through the writer.

    execute_many is bound by the call site to the session writer (INV-21);
    this module never opens a connection. Must be called after t1.
    Portable SQL (BL-2b-3): no INSERT OR IGNORE.
    """
    if gate.is_inside():
        raise StageContractError("persist inside measurement window")
    if final:
        recorder.finalize()  # close unreached stages only on the last write
    g = sg.GRAPHS[recorder.graph_id]
    now = datetime.now(timezone.utc).isoformat()
    execute_many(INSERT_GRAPH, [(recorder.graph_hash, g["graph_id"], g["graph_version"],
                                 sg.canonical_json(g), now, recorder.graph_hash)])
    rows = recorder.rows()
    if rows:
        execute_many(INSERT_EVENT, rows)
    recorder._written.update(r[4] for r in rows)  # r[4] is stage_id
    return len(rows)


_log = logging.getLogger("alems.observability.stages")


class _NoopErrors(object):
    """Stand in when the error module cannot load: observability never breaks the run."""

    @staticmethod
    def push_stage(recorder, stage_id):
        return None

    @staticmethod
    def pop_stage(token):
        return None

    @staticmethod
    def capture_coded(exc, *args, **kwargs):
        return None, None


def _obs_errors():
    """The C-ERR module, or a no op stand in if it fails to import (5.2a)."""
    try:
        from core.observability import errors
        return errors
    except Exception:  # noqa: BLE001  counted nowhere: no gate state is reachable
        return _NoopErrors


def _stage_failure(exc, prior_reason):
    """
    error_ref and reason for an exception leaving a stage (2d design 7.1, 7.4).

    BaseException that is not Exception (KeyboardInterrupt, GeneratorExit)
    is recorded as before and not captured.
    """
    if not isinstance(exc, Exception):
        return None, prior_reason or "exception"
    ref, code = _obs_errors().capture_coded(exc, component="core.observability.stages",
                                            note=False)
    reason = code or "exception"
    if ref is None:
        reason += ";capture_failed"
    return ref, ("%s;%s" % (prior_reason, reason)) if prior_reason else reason


def stage_or_noop(stages, stage_id):
    """Stage context when a recorder is bound, else a no op context."""
    if stages is None:
        return nullcontext({"counts": {}, "outcome": None, "reason": None})
    return stages.stage(stage_id)


def persist_after_run(recorder, db, final=True):
    # type: (Optional[StageRecorder], object, bool) -> int
    """
    Write a recorder's rows through the store's writer connection in one
    transaction, after the run. Observability is subordinate (master 5.2a):
    any failure is logged and swallowed, never raised into the run path.
    A recorder that saw no stage at all writes nothing (no orphan rows).
    final=False writes the stages finished so far (phase 1); final=True also
    closes unreached stages (phase 2). A row is never written twice.
    """
    if recorder is None or not recorder.events:
        return 0
    try:
        inner = getattr(db, "db", None)
        conn = getattr(inner, "conn", None) or getattr(db, "conn", None)
        with db.transaction():
            return persist(recorder, lambda sql, rows: conn.executemany(sql, rows), final)
    except Exception as exc:  # never alters the run (5.2a)
        _log.warning("stage rows not persisted run_uid=%s: %s", recorder.run_uid, exc)
        return 0
