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
import os
import time
import uuid
from contextlib import contextmanager
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

    def attach_run_id(self, run_id):
        # type: (int) -> None
        """Attach run_id once insert_run returned (EEI-4)."""
        self.run_id = run_id

    def _blocker(self, stage_id):
        # type: (str) -> Optional[str]
        """First required predecessor whose terminal status blocks."""
        for dep in sg.requires_of(self.graph_id, stage_id):
            ev = self.events.get(dep)
            if ev is not None and ev["status"] in _BLOCKING:
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
        try:
            yield info
        except BaseException:
            self._record(stage_id, "failed", start, reason=info["reason"] or "exception",
                         counts=info["counts"])
            raise
        self._close_ok(stage_id, start, info)

    def _close_ok(self, stage_id, start, info):
        """Apply the declared stage contract to a normal exit (design 14.5)."""
        nd = sg.node(self.graph_id, stage_id) or {}
        counts = info["counts"] or {}
        outcome = info["outcome"]
        if outcome == "unavailable" and not nd.get("unavailable_ok"):
            self._record(stage_id, "failed", start, reason="undeclared_unavailable",
                         counts=counts)
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
        """Rows in event_seq order for the stage_event insert."""
        now = datetime.now(timezone.utc).isoformat()
        evs = sorted(self.events.values(), key=lambda e: (e["event_seq"] is None,
                                                          e["event_seq"] or 0))
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


def persist(recorder, execute_many):
    # type: (StageRecorder, Callable[[str, List[tuple]], None]) -> int
    """
    Write the graph (idempotent) and all stage rows through the writer.

    execute_many is bound by the call site to the session writer (INV-21);
    this module never opens a connection. Must be called after t1.
    Portable SQL (BL-2b-3): no INSERT OR IGNORE.
    """
    if gate.is_inside():
        raise StageContractError("persist inside measurement window")
    recorder.finalize()
    g = sg.GRAPHS[recorder.graph_id]
    now = datetime.now(timezone.utc).isoformat()
    execute_many(INSERT_GRAPH, [(recorder.graph_hash, g["graph_id"], g["graph_version"],
                                 sg.canonical_json(g), now, recorder.graph_hash)])
    rows = recorder.rows()
    execute_many(INSERT_EVENT, rows)
    return len(rows)
