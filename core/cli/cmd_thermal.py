"""
core/cli/cmd_thermal.py: `alems thermal run --profile <file> [--dry-run]`.

A thermal profile is an experiment config with a session_plan block (and
usually a recording block). The command checks the plan before anything
starts; --dry-run prints the planned steps (assuming every goal succeeds)
and runs nothing; otherwise run_experiment runs the profile in this process,
so the store resolver, safety guard and recording behave as for `alems run`.
"""

import argparse
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _args(argv):
    # type: (List[str]) -> argparse.Namespace
    """Command line: action run, --profile, --dry-run, --max-steps."""
    p = argparse.ArgumentParser(prog="alems thermal")
    p.add_argument("action", choices=["run"])
    p.add_argument("--profile", required=True, help="experiment config with a session_plan block")
    p.add_argument("--dry-run", action="store_true", help="check the plan and print its steps; run nothing")
    p.add_argument("--max-steps", type=int, default=500, help="dry run: stop listing after this many steps")
    return p.parse_args(argv)


def _load(path):
    # type: (str) -> Dict[str, Any]
    """Profile YAML; refuses one without a session_plan block."""
    import yaml
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not doc.get("session_plan"):
        raise ValueError("%s has no session_plan block" % path)
    return doc


def dry_run(doc, max_steps):
    # type: (Dict[str, Any], int) -> List[Any]
    """Planned steps, every goal assumed solved; raises SessionPlanError like a real start."""
    from alems_sdk.session_plan import Outcome
    from core.execution.session_plan import SessionPlanError, check_step, load_plan
    plan_cfg = doc["session_plan"]
    tasks = [t["id"] for t in doc.get("tasks") or []]
    providers = [p["name"] for p in doc.get("providers") or []]
    cls = load_plan(plan_cfg.get("plugin"))
    errors = cls.validate(dict(plan_cfg.get("params") or {}))
    if errors:
        raise SessionPlanError("; ".join(errors))
    plan = cls()
    try:
        plan.start(dict(plan_cfg.get("params") or {}), {"tasks": tasks, "providers": providers, "recorder": None})
    except ValueError as exc:
        raise SessionPlanError("plan refused the session: %s" % exc)
    history = []  # type: List[Any]
    while len(history) < max_steps:
        step = plan.next_step(list(history))
        check_step(step, tasks, providers)
        if step.kind == "done":
            break
        history.append(Outcome(step=step, status="completed", success=True, planned_s=step.seconds or step.hold_s))
    return history


def _print_steps(history):
    # type: (List[Any]) -> None
    """One line per planned step."""
    for i, o in enumerate(history, 1):
        s = o.step
        what = "%s %s" % (s.task_id, s.workflow) if s.task_id else "%.1f s %s" % (s.seconds, s.label)
        if s.kind == "load_step":
            what += " hold %.1f s" % s.hold_s
        print("%4d  %-9s %s" % (i, s.kind, what))


def _run_profile(path):
    # type: (str) -> int
    """Run the profile through run_experiment in this process (same pattern as `alems run`)."""
    script = Path(__file__).resolve().parents[2] / "core" / "execution" / "tests" / "run_experiment.py"
    spec = importlib.util.spec_from_file_location("_alems_run_experiment", str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    saved = sys.argv
    sys.argv = ["alems-thermal", "--config", str(path)]
    try:
        result = mod.main()
        return result if isinstance(result, int) else 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = saved


def handle_thermal(argv):
    # type: (List[str]) -> int
    """CLI entry; returns a process exit code (2 = invalid profile or plan)."""
    from core.execution.session_plan import SessionPlanError
    a = _args(argv)
    try:
        doc = _load(a.profile)
        steps = dry_run(doc, a.max_steps)
    except (ValueError, OSError) as exc:      # SessionPlanError is a ValueError
        print("alems thermal: %s" % exc, file=sys.stderr)
        return 2
    goals = sum(1 for o in steps if o.step.kind in ("goal", "load_step"))
    print("plan %s: %d steps, %d goal or load steps (all goals assumed solved)"
          % (doc["session_plan"].get("plugin"), len(steps), goals))
    if a.dry_run:
        _print_steps(steps)
        return 0
    return _run_profile(a.profile)
