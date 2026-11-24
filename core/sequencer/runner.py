"""
core/sequencer/runner.py: execute a resolved plan step by step.

Order of work per step: settle (recorded), commit a running attempt, run the
kind, close the attempt conditionally in one transaction (with any kind store
work, e.g. the idle baseline row). Fail safe rules (45.4c1):
  pre idle failed     treatment and post skipped (or treatment run, if the
                      plan says on_baseline_failure: run_treatment)
  treatment failed    post idle still runs (drift evidence)
  store write failed  dispatch stops; session stopped with the reason
  KeyboardInterrupt   current attempt interrupted; session interrupted
A resource lock (one sequencer per store) is held for the whole session.
A thermal readiness gate (gates.py) runs after the settle time; counterbalanced
order comes from plan.cycle_orders().
"""

import fcntl
import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.sequencer import plan as plan_mod
from core.sequencer import gates
from core.sequencer.context import pop_link_failure
from core.sequencer.kinds import all_kinds
from core.sequencer.store import SequenceStore, SequencerStoreError

logger = logging.getLogger(__name__)


class ResourceBusy(RuntimeError):
    """Another sequencer owns this store."""


def acquire_lock(store_path):
    # type: (str) -> Any
    """Exclusive, non blocking lock next to the store; held until the process ends."""
    fh = open(store_path + ".sequencer.lock", "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise ResourceBusy("another sequencer owns %s" % store_path)
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


class Sequencer:
    """Runs one session; store, config loader and db supplied by the CLI."""

    def __init__(self, db, config_loader):
        self.store = SequenceStore(db)
        self.ctx = {"db": db, "store": self.store, "config_loader": config_loader}

    def start(self, plan):
        # type: (Dict[str, Any]) -> int
        """New session: materialise all steps, then dispatch them in order."""
        self.store.check_schema()
        steps = plan_mod.materialize(plan)
        core_plan = {k: v for k, v in plan.items() if k not in ("path", "source_sha256")}
        sid = self.store.create_session(plan, plan_mod.plan_sha256(core_plan),
                                        plan_mod.realized_order(steps), {})
        for step, step_id in zip(steps, self.store.insert_steps(sid, steps)):
            step["step_id"] = step_id
        return self._dispatch(sid, plan, steps)

    def resume(self, session_id, plan):
        # type: (int, Dict[str, Any]) -> int
        """Close stale running attempts (we hold the lock, so their worker is gone), then continue."""
        self.store.check_schema()
        for aid in self.store.running_attempts(session_id):
            self.store.finish_attempt(aid, "interrupted", {"reason_code": "resumed",
                                      "reason_detail": "worker ended before completion was recorded"})
        return self._dispatch(session_id, plan, self.store.pending_steps(session_id))

    def _dispatch(self, sid, plan, steps):
        # type: (int, Dict[str, Any], List[Dict[str, Any]]) -> int
        """Run steps; returns 0 completed, 1 stopped, 130 interrupted."""
        failed_pre = set()  # type: set
        treatments = {s["bracket_id"]: s for s in steps if s["role"] == "treatment"}
        self.ctx["prepared"] = {}
        try:
            for step in steps:
                if step["role"] == "pre" and step["bracket_id"] in treatments:
                    self._prepare(treatments[step["bracket_id"]])
                self._one(plan, step, failed_pre)
        except SequencerStoreError as exc:
            logger.error("sequencer: store failure, dispatch stopped: %s", exc)
            self._safe_end(sid, "stopped", str(exc))
            return 1
        except KeyboardInterrupt:
            # The step in progress is closed as interrupted; committed runs keep their links.
            for aid in self.store.running_attempts(sid):
                self.store.finish_attempt(aid, "interrupted", {"reason_code": "keyboard_interrupt"})
            self._safe_end(sid, "interrupted", "keyboard interrupt")
            return 130
        self.store.end_session(sid, "completed")
        return 0

    def _one(self, plan, step, failed_pre):
        # type: (Dict[str, Any], Dict[str, Any], set) -> None
        """Settle, attempt, run, close; applies the skip rule for failed pre idle."""
        if step["bracket_id"] in failed_pre and step["role"] != "pre":
            if step["role"] == "post" or plan["on_baseline_failure"] != "run_treatment":
                self.store.skip_step(step["step_id"], "pre_baseline_failed",
                                     "pre idle of bracket %s failed" % step["bracket_id"])
                return
        settle_start = time.monotonic_ns()
        time.sleep(float(step["params"].get("settle_s", plan["settle_s"])))
        settle_end = time.monotonic_ns()
        gate = gates.wait_ready(self._thermal_read, gates.gate_config(plan.get("gate")))
        if gate["outcome"] == "timeout" and (plan.get("gate") or {}).get("on_timeout", "skip") == "skip":
            self.store.skip_step(step["step_id"], "gate_timeout", "chip above threshold after max wait", gate)
            if step["role"] == "pre":
                failed_pre.add(step["bracket_id"])
            return
        aid = self.store.begin_attempt(step["step_id"], settle_start, settle_end, gate)
        pop_link_failure(aid)   # clear any stale entry for this id before the step runs
        outcome = self._run_kind(step, aid)
        link_failure = pop_link_failure(aid)
        if link_failure:
            # A run of this step could not be linked and was rolled back: stop dispatch.
            self.store.finish_attempt(aid, "failed", {"reason_code": "link_failed", "reason_detail": link_failure})
            raise SequencerStoreError(link_failure)
        extra = outcome.pop("extra_fn", None)
        self.store.finish_attempt(aid, outcome.pop("status"), outcome, extra)
        if step["role"] == "pre" and outcome.get("reason_code"):
            failed_pre.add(step["bracket_id"])

    def _prepare(self, treatment):
        # type: (Dict[str, Any]) -> None
        """Plugin preparation (e.g. a probe) before the pre idle, so it cannot warm the bracket."""
        from core.sequencer.kinds import prepare_bracket
        try:
            self.ctx["prepared"][treatment["bracket_id"]] = prepare_bracket(treatment, self.ctx)
        except Exception as exc:   # treatment will report it; the bracket continues
            logger.warning("sequencer: prepare failed for %s: %s", treatment["bracket_id"], exc)
            self.ctx["prepared"][treatment["bracket_id"]] = {"prepare_error": repr(exc)}

    def _thermal_read(self):
        # type: () -> Dict[str, float]
        """Platform temperatures (sensor to Celsius); empty when no reader or sensors."""
        if "thermal" not in self.ctx:
            from core.sequencer.kinds import thermal_reader
            self.ctx["thermal"] = thermal_reader(self.ctx.get("config_loader"))
        reader = self.ctx["thermal"]
        return reader.read_all_thermal() if reader is not None else {}

    def _run_kind(self, step, attempt_id):
        # type: (Dict[str, Any], int) -> Dict[str, Any]
        """Run the step kind; any kind error becomes a failed outcome (store errors propagate)."""
        ctx = dict(self.ctx, attempt_id=attempt_id)
        try:
            return all_kinds()[step["kind"]](step, ctx)
        except (SequencerStoreError, KeyboardInterrupt):
            raise
        except Exception as exc:
            logger.warning("sequencer: step %s failed: %s", step["planned_index"], exc)
            return {"status": "failed", "reason_code": "exception", "reason_detail": repr(exc)}

    def _safe_end(self, sid, status, reason):
        # type: (int, str, Optional[str]) -> None
        """End the session if the store still accepts writes; log otherwise."""
        try:
            self.store.end_session(sid, status, reason)
        except SequencerStoreError as exc:
            logger.error("sequencer: could not record session end: %s", exc)
