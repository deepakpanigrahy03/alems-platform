"""
core/sequencer/context.py: which sequencer attempt the current run belongs to,
and the mandatory run to attempt link written inside persist_raw().

Why a context variable: the attempt id must reach run persistence without
touching the three execution paths, and without a process wide setting that
could attach an unrelated concurrent run to the active attempt. A ContextVar
is visible only to code running inside the sequencer's step context.
At a subprocess boundary the parent passes ALEMS_SEQ_ATTEMPT_ID in that
child's environment only; it is read here as a fallback.

Guarantee: when an attempt id is present, the link is mandatory. Any failure
(missing table, unknown or finished attempt, insert error) raises inside the
caller's transaction, so the run and its children roll back together.
"""

import contextlib
import contextvars
import os
from typing import Iterator, Optional

ENV_ATTEMPT = "ALEMS_SEQ_ATTEMPT_ID"
_ATTEMPT = contextvars.ContextVar("alems_seq_attempt_id", default=None)


class SequencerLinkError(RuntimeError):
    """Run cannot be linked to its attempt; the run transaction must roll back."""


# Link failures per attempt: a caller of persist_raw may log and continue, so
# the sequencer checks this after each step and stops dispatch (store failure).
_LINK_FAILURES = {}  # type: dict


def pop_link_failure(attempt_id):
    # type: (int) -> Optional[str]
    """Recorded link failure of an attempt, removed once read."""
    return _LINK_FAILURES.pop(attempt_id, None)


def current_attempt():
    # type: () -> Optional[int]
    """Attempt id of the current context, else of this process's environment."""
    aid = _ATTEMPT.get()
    if aid is not None:
        return aid
    raw = os.environ.get(ENV_ATTEMPT)
    return int(raw) if raw else None


@contextlib.contextmanager
def attempt_scope(attempt_id):
    # type: (int) -> Iterator[None]
    """Runs persisted inside this block are linked to attempt_id."""
    token = _ATTEMPT.set(attempt_id)
    try:
        yield
    finally:
        _ATTEMPT.reset(token)


def link_run(db, run_id, workflow_type, rep_num):
    # type: (object, int, str, Optional[int]) -> None
    """
    Write the run to attempt link; call inside persist_raw()'s transaction.

    No attempt in context: ordinary run, nothing to do. Attempt in context:
    the attempt must exist and be running, and the insert must succeed;
    otherwise SequencerLinkError rolls back the run.
    """
    aid = current_attempt()
    if aid is None:
        return
    try:
        row = db.db.conn.execute(
            "SELECT execution_status FROM energy_validation_seq_attempts WHERE attempt_id = ?",
            (aid,)).fetchone()
        if not row or row[0] != "running":
            raise SequencerLinkError("attempt %s not running (%s)" % (aid, row[0] if row else "absent"))
        # child_seq = commit order within the attempt; rep_num and workflow_type
        # keep the planned position, so an incomplete pair keeps its meaning.
        db.db.conn.execute(
            "INSERT INTO energy_validation_seq_attempt_runs "
            "(attempt_id, run_id, child_seq, rep_num, workflow_type) "
            "SELECT ?, ?, COALESCE(MAX(child_seq) + 1, 0), ?, ? "
            "FROM energy_validation_seq_attempt_runs WHERE attempt_id = ?",
            (aid, run_id, rep_num, workflow_type, aid))
    except SequencerLinkError as exc:
        _LINK_FAILURES[aid] = str(exc)
        raise
    except Exception as exc:  # missing table, FK or UNIQUE violation
        _LINK_FAILURES[aid] = "run %s not linked to attempt %s: %s" % (run_id, aid, exc)
        raise SequencerLinkError(_LINK_FAILURES[aid])
