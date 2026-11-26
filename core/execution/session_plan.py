"""
core/execution/session_plan.py: plan-driven experiment session.

When an experiment config has a session_plan block, run_experiment runs this
loop instead of its fixed tasks x providers x repetitions loop (which stays
unchanged when no plan is configured). The plan (entry point group
alems.harness.pacing) chooses each step; this module executes it:

  goal       one goal through execute_goal (retries, persistence, safety gate)
  load_step  goals of one task back to back until hold_s has passed
  wait       a planned pause (the safety guard may still wait longer before
             the next goal; that wait is visible as first_run_start_ns minus
             step_start_utc_ns of the next goal, never counted as pacing)
  done       end the session

One experiment row per (task, provider) is created on first use and reused, so
experiment identity and provider/task relations stay as in the fixed loop.
Outcomes are read from the goal tables after each goal; the plan always sees
what really happened, failures included. The plan's own state lives in
memory: a crash ends the session (no silent resume, so no step is skipped or
repeated without a record).
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from alems_sdk.session_plan import STEP_KINDS, WORKFLOWS, Outcome, Step

logger = logging.getLogger(__name__)

PLAN_GROUP = "alems.harness.pacing"


class SessionPlanError(ValueError):
    """The session plan or its configuration is invalid; raised before any step runs."""


def load_plan(name):
    # type: (str) -> Any
    """Plan class registered under alems.harness.pacing with this name."""
    from importlib.metadata import entry_points
    eps = entry_points()
    group = eps.select(group=PLAN_GROUP) if hasattr(eps, "select") else eps.get(PLAN_GROUP, [])
    for ep in group:
        if ep.name == name:
            return ep.load()
    raise SessionPlanError("no session plan named %r in %s" % (name, PLAN_GROUP))


def check_step(step, tasks, providers):
    # type: (Step, List[str], List[str]) -> None
    """Refuse a malformed step before it runs (a plan bug must never run the wrong work)."""
    if step.kind not in STEP_KINDS:
        raise SessionPlanError("unknown step kind %r" % step.kind)
    if step.kind in ("goal", "load_step"):
        if step.task_id not in tasks:
            raise SessionPlanError("step task %r is not in the session's tasks %s" % (step.task_id, tasks))
        if step.workflow not in WORKFLOWS:
            raise SessionPlanError("step workflow %r is not one of %s" % (step.workflow, WORKFLOWS))
        if step.provider is not None and step.provider not in providers:
            raise SessionPlanError("step provider %r is not in the session's providers" % step.provider)
    if step.kind == "load_step" and not (step.hold_s and step.hold_s > 0):
        raise SessionPlanError("load_step needs hold_s above 0")
    if step.kind == "wait" and (step.seconds is None or step.seconds < 0):
        raise SessionPlanError("wait needs seconds >= 0 (a negative pause is never clamped)")


def run_session_plan(session_plan, tasks, providers, deps, plan_cls=None, recorder=None, config_path=None):
    # type: (Dict[str, Any], List[str], List[str], Dict[str, Callable], Any, Any, Optional[str]) -> List[Outcome]
    """
    Run one plan-driven session and return every outcome.

    Args:
        session_plan: {"plugin": name, "params": {...}} from the experiment config.
        tasks, providers: ids available to the plan (from the experiment config).
        deps: execution functions (production_deps() in real runs, fakes in tests).
        plan_cls: plan class (tests); None loads it from the entry point group.
        recorder: the open continuous recording, passed to the plan for its spans.
        config_path: the experiment config file, so the plan can record which profile ran.
    """
    name = (session_plan or {}).get("plugin")
    params = dict((session_plan or {}).get("params") or {})
    cls = plan_cls or load_plan(name)
    errors = cls.validate(params)
    if errors:
        raise SessionPlanError("; ".join(errors))
    plan = cls()
    try:
        plan.start(params, {"tasks": list(tasks), "providers": list(providers), "recorder": recorder,
                            "config_path": config_path})
    except ValueError as exc:            # e.g. a computed pause below zero: refused before any step
        raise SessionPlanError("plan %s refused the session: %s" % (name or cls.NAME, exc))
    history = []  # type: List[Outcome]
    try:
        _loop(plan, history, tasks, providers, deps)
    finally:
        deps["finish"]()                  # experiment rows closed even after an error
    return history


def _loop(plan, history, tasks, providers, deps):
    # type: (Any, List[Outcome], List[str], List[str], Dict[str, Callable]) -> None
    """Ask the plan, check the step, run it, record the outcome; until done."""
    while True:
        step = plan.next_step(list(history))
        check_step(step, tasks, providers)
        if step.kind == "done":
            logger.info("session plan: done after %d steps", len(history))
            return
        history.append(_run_step(step, providers, deps, len(history) + 1))


def _run_step(step, providers, deps, index):
    # type: (Step, List[str], Dict[str, Callable], int) -> Outcome
    """Dispatch one step to its runner."""
    logger.info("session plan step %d: %s %s %s", index, step.kind, step.task_id or "", step.workflow or step.label or "")
    if step.kind == "wait":
        return _run_wait(step, deps)
    provider = step.provider or providers[0]
    if step.kind == "goal":
        return _run_goal(step, provider, deps)
    return _run_load(step, provider, deps)


def _run_wait(step, deps):
    # type: (Step, Dict[str, Callable]) -> Outcome
    """Planned pause; actual length measured on the monotonic clock."""
    t_utc, t0 = deps["utc_ns"](), deps["mono"]()
    deps["wait"](step.seconds, step.label or "planned pause")
    return Outcome(step=step, status="completed", step_start_utc_ns=t_utc, end_utc_ns=deps["utc_ns"](),
                   planned_s=step.seconds, actual_s=deps["mono"]() - t0)


def _run_goal(step, provider, deps):
    # type: (Step, str, Dict[str, Callable]) -> Outcome
    """One goal; an execution error is a failed outcome the plan sees (start refusals propagate)."""
    out = Outcome(step=step, status="completed", step_start_utc_ns=deps["utc_ns"]())
    _execute_into(out, step, provider, deps)
    out.end_utc_ns = deps["utc_ns"]()
    return out


def _run_load(step, provider, deps):
    # type: (Step, str, Dict[str, Callable]) -> Outcome
    """Goals back to back until hold_s has passed; success only if every goal was solved."""
    out = Outcome(step=step, status="completed", step_start_utc_ns=deps["utc_ns"](), planned_s=step.hold_s)
    t0 = deps["mono"]()
    while deps["mono"]() - t0 < step.hold_s:
        _execute_into(out, step, provider, deps)
        if out.status == "error":
            break                         # an execution error ends the hold early (recorded)
    out.actual_s = deps["mono"]() - t0
    out.hold_achieved = out.actual_s >= step.hold_s
    out.end_utc_ns = deps["utc_ns"]()
    return out


def _execute_into(out, step, provider, deps):
    # type: (Outcome, Step, str, Dict[str, Callable]) -> None
    """Run one goal and merge its result into out (ids appended, success and-ed)."""
    try:
        goal_id = deps["execute"](step.task_id, provider, step.workflow)
    except deps["refusal"]:
        raise                             # the safety guard refused: the session ends
    except Exception as exc:
        logger.warning("session plan: goal %s/%s raised %s", step.task_id, step.workflow, exc)
        out.status, out.success = "error", False
        out.failure_reason = "%s: %s" % (type(exc).__name__, exc)
        return
    res = deps["result"](goal_id)
    out.goal_ids.append(goal_id)
    out.attempt_ids.extend(res["attempt_ids"])
    out.run_ids.extend(res["run_ids"])
    if out.first_run_start_ns is None:
        out.first_run_start_ns = res["first_run_start_ns"]
    if res.get("last_run_end_ns") is not None:
        out.last_run_end_ns = res["last_run_end_ns"]
    solved = bool(res["success"])
    out.success = solved if out.success is None else (out.success and solved)
    if not solved and out.status == "completed":
        out.status = "failed"
        out.failure_reason = res.get("failure_reason")


def production_deps(harness, runner, config, args, goal_tracker, retry_coordinator):
    # type: (Any, Any, Any, Any, Any, Any) -> Dict[str, Callable]
    """Execution functions backed by the real harness, runner and goal tables."""
    from core.observability import status as obs_status
    from core.safety.thermal_guard import GuardRefused
    if not getattr(args, "save_db", False):
        # outcomes are read from the goal tables, so a plan session always stores its goals
        raise SessionPlanError("a session plan needs save_db (outcomes come from the goal tables)")
    rt = _Runtime(harness, runner, config, args, goal_tracker, retry_coordinator)
    return {"execute": rt.execute, "result": rt.result, "finish": rt.finish, "wait": obs_status.wait,
            "utc_ns": time.time_ns, "mono": time.monotonic, "refusal": GuardRefused}


class _Runtime(object):
    """Per (task, provider) executors and experiment rows, created on first use and reused."""

    def __init__(self, harness, runner, config, args, goal_tracker, retry_coordinator):
        # type: (Any, Any, Any, Any, Any, Any) -> None
        """Store the run_experiment objects; nothing is opened until the first goal."""
        self.harness, self.runner, self.config, self.args = harness, runner, config, args
        self.goal_tracker, self.retry_coordinator = goal_tracker, retry_coordinator
        self.db = self.hw_id = self.env_id = self.policy = None
        self.units = {}   # type: Dict[tuple, Dict[str, Any]]
        self.rep = {}     # type: Dict[tuple, int]

    def _open_db(self):
        # type: () -> None
        """Store, hardware row and retry policy, once per session (as the fixed loop does per task)."""
        if self.db is not None:
            return
        self.db, self.hw_id, self.env_id = self.runner.setup_database()
        self.config.sync_task_categories(self.db.db.conn)
        self.policy = _policy(self.retry_coordinator, self.db, self.args)

    def _unit(self, task_id, provider):
        # type: (str, str) -> Dict[str, Any]
        """Executors and experiment row for one (task, provider)."""
        key = (task_id, provider)
        if key not in self.units:
            self._open_db()
            self.units[key] = _make_unit(self, task_id, provider)
        return self.units[key]

    def execute(self, task_id, provider, workflow):
        # type: (str, str, str) -> Optional[int]
        """One goal through execute_goal; rep_num counts goals per (task, provider, workflow)."""
        from core.execution.goal_execution_manager import execute_goal
        from core.retry.retry_adapter import get_retry_adapter
        u = self._unit(task_id, provider)
        rkey = (task_id, provider, workflow)
        self.rep[rkey] = self.rep.get(rkey, 0) + 1
        u["goals"] += 1
        a = self.args
        return execute_goal(
            db=self.db, exp_id=u["exp_id"], hw_id=self.hw_id, harness=self.harness,
            executor=u["executors"][workflow], task=u["task"], workflow_type=workflow,
            rep_num=self.rep[rkey], goal_tracker=self.goal_tracker, policy=self.policy,
            failure_injector=getattr(a, "failure_injector", None), repetitions=1,
            retry_adapter=get_retry_adapter(a),
            recovery_policy_id=getattr(a, "recovery_policy_strategy", "full_restart"),
            cache_collector=getattr(a, "cache_collector", None),
            quality_enabled=getattr(a, "quality_enabled", False))

    def result(self, goal_id):
        # type: (Optional[int]) -> Dict[str, Any]
        """Success, attempts, runs and first run start of one goal, read from the goal tables."""
        return _goal_result(self.db.db.conn, goal_id)

    def finish(self):
        # type: () -> None
        """Mark each experiment row of the session completed with its goal count."""
        for u in self.units.values():
            self.runner.update_status(self.db, u["exp_id"], "completed", u["goals"])


def _make_unit(rt, task_id, provider):
    # type: (_Runtime, str, str) -> Dict[str, Any]
    """Preflight, executors and experiment row for one (task, provider), as the fixed loop builds them."""
    from core.execution.agentic import AgenticExecutor
    from core.execution.linear import LinearExecutor
    from core.utils.preflight import preflight
    from core.utils.task_loader import load_tasks
    task = next(t for t in load_tasks() if t["id"] == task_id)
    models = rt.runner.config.list_models(provider)
    if not models:
        raise SessionPlanError("no models for provider %s" % provider)
    model_id = getattr(rt.args, "model", None) or models[0]["model_id"]
    cfg = rt.runner.config.get_model_config_v2(provider, model_id)
    preflight(type("obj", (object,), {"config": cfg})(), provider)
    exp_id = rt.runner.create_experiment(
        rt.db, task["id"], task["name"], provider, cfg, rt.args.country, 1, rt.hw_id, rt.env_id,
        optimizer=False, experiment_type=getattr(rt.args, "experiment_type", "normal"),
        experiment_goal=getattr(rt.args, "experiment_goal", None),
        declared_dedicated_machine=getattr(rt.args, "declared_dedicated_machine", None),
        workflow_mode="comparison")
    task_dict = dict(task, id=task.get("id"), name=task.get("name"), prompt=task["prompt"],
                     meta=task.get("meta", {}), tool_graph=task.get("tool_graph"))
    return {"exp_id": exp_id, "task": task_dict, "goals": 0,
            "executors": {"linear": LinearExecutor(cfg), "agentic": AgenticExecutor(cfg)}}


def _policy(retry_coordinator, db, args):
    # type: (Any, Any, Any) -> Any
    """Retry policy with the experiment config's overrides, the same rule as the fixed loop's retry path."""
    from dataclasses import replace
    policy = retry_coordinator.load_policy(db.db.conn, getattr(args, "policy_name", "default"))
    keys = ("retry_on_timeout", "retry_on_tool_error", "retry_on_api_error", "retry_on_wrong_answer",
            "max_retries", "backoff_seconds")
    overrides = {k: getattr(args, k) for k in keys if hasattr(args, k)}
    return replace(policy, **overrides) if overrides else policy


def _goal_result(conn, goal_id):
    # type: (Any, Optional[int]) -> Dict[str, Any]
    """Read-only lookup; an unknown goal (execute_goal returned None) is a failure with no ids."""
    if goal_id is None:
        return {"success": False, "attempt_ids": [], "run_ids": [], "first_run_start_ns": None,
                "last_run_end_ns": None, "failure_reason": "execute_goal returned no goal"}
    row = conn.execute("SELECT success, status FROM goal_execution WHERE goal_id = ?", (goal_id,)).fetchone()
    att = conn.execute("SELECT a.attempt_id, a.run_id, r.start_time_ns, r.end_time_ns FROM goal_attempt a "
                       "LEFT JOIN runs r ON r.run_id = a.run_id WHERE a.goal_id = ? ORDER BY a.attempt_id",
                       (goal_id,)).fetchall()
    starts = [s for _, _, s, _ in att if s is not None]
    ends = [e for _, _, _, e in att if e is not None]
    return {"success": bool(row and row[0]), "attempt_ids": [a for a, _, _, _ in att],
            "run_ids": [r for _, r, _, _ in att if r is not None],
            "first_run_start_ns": min(starts) if starts else None,
            "last_run_end_ns": max(ends) if ends else None,
            "failure_reason": None if (row and row[0]) else "goal status %s" % (row[1] if row else "missing")}
