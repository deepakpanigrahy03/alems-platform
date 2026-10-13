#!/usr/bin/env python3
"""
RAF LH 01 S4 smoke: one goal per failure class through execute_goal.

Run from the pilot sandbox so the store resolver picks the sandbox store:
    cd ~/sandboxes/raf-mlsys-2027
    python3 ~/mydrive/alems-platform/scripts/raf/s4_smoke.py --provider <p> --model <m>

Reuses run_experiment construction (parse_arguments, ExperimentRunner,
ExperimentHarness, setup_database, create_experiment, retry policy and
adapter, execute_goal). Adds per goal only what the pilot needs:
    ScenarioInjector rule   rate 1.0, max_injections 1, location phase and ordinal
    PreAttempt hook          cache resets and tool restore outside windows
    _raf_context             goal key and seed (policy written by PreAttempt)
No new core code; nothing here runs inside a measurement window.
"""

import argparse
import os
import sys
import uuid
from dataclasses import replace as dc_replace

ENGINE = os.path.expanduser("~/mydrive/alems-platform")
sys.path.insert(0, ENGINE)

# (class, call type phase, ordinal, policy): early point 2 for every class,
# each policy cell covered once, both cache modes and both depths exercised.
CASES = [
    ("rate_limit", "llm", 2, "FR_warm"),
    ("json_parse", "llm", 2, "SR_cold"),
    ("tool_error", "tool", 2, "SR_warm"),
    ("timeout", "tool", 2, "FR_cold"),
]


def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", required=True, help="provider of the pinned Qwen entry")
    ap.add_argument("--model", required=True, help="model_id of the pinned Qwen entry")
    ap.add_argument("--task", default="bfcl_multi_turn_base_7")
    ap.add_argument("--b0", action="store_true", help="also run one B0 goal first")
    return ap.parse_args()


def _run_args(model: str):
    """run_experiment's own argument namespace with defaults, then pilot values."""
    from core.execution.tests import run_experiment as rx
    saved = sys.argv
    sys.argv = [saved[0]]
    try:
        args = rx.parse_arguments()
    finally:
        sys.argv = saved
    args.save_db = True
    args.model = model
    args.workflow_mode = "agentic"
    args.experiment_type = "failure_injection"   # injector allows only this and retry_study
    args.experiment_goal = "RAF LH 01 S4 smoke"
    args.quality_enabled = True
    args.max_retries = 1
    return rx, args


def _policy(rx, conn, args):
    """Default policy with one retry allowed for every pilot failure class."""
    policy = rx._retry_coordinator.load_policy(conn, "default")
    return dc_replace(policy, max_retries=1, backoff_seconds=0,
                      retry_on_timeout=True, retry_on_tool_error=True,
                      retry_on_api_error=True)


def _injector(cls: str, phase: str, ordinal: int, goal_key: str):
    from core.injection.scenario_injector import ScenarioInjector
    rule = {"type": cls, "rate": 1.0, "max_injections": 1,
            "location": {"phase": phase, "step_index": ordinal, "tool_name": "any"}}
    return ScenarioInjector([rule], scenario_id="raf_s4_" + goal_key,
                            experiment_type="failure_injection")


def main() -> int:
    a = _args()
    rx, args = _run_args(a.model)
    from core.config_loader import ConfigLoader
    from core.execution.experiment_runner import ExperimentRunner
    from core.execution.harness import ExperimentHarness
    from core.execution.agentic import AgenticExecutor
    from core.execution.goal_execution_manager import execute_goal
    from core.retry.retry_adapter import get_retry_adapter
    from core.utils.task_loader import load_tasks
    from alems_plugin_bfcl.prepare import PreAttempt
    from core.execution.frameworks.bootstrap import register_all_frameworks

    # The harness reads framework_registry but never fills it; the caller must
    # (idempotent). Without this, bfcl_react raises NoFrameworkError.
    register_all_frameworks()
    config = ConfigLoader()
    harness = ExperimentHarness(config)
    runner = ExperimentRunner(config, args)
    task = next(t for t in load_tasks() if t["id"] == a.task)
    model_cfg = runner.config.get_model_config_v2(a.provider, a.model)
    base_url = model_cfg.get("base_url") or model_cfg.get("api_endpoint") or ""

    db, hw_id, env_id = runner.setup_database()
    exp_id = runner.create_experiment(
        db, task["id"], task["name"], a.provider, model_cfg, "US", 1, hw_id, env_id,
        experiment_type="failure_injection", experiment_goal="RAF LH 01 S4 smoke",
        workflow_mode="agentic")
    print("store exp_id=%s base_url=%s" % (exp_id, base_url))

    policy = _policy(rx, db.db.conn, args)
    retry_adapter = get_retry_adapter(args)
    cases = ([("none", None, None, "B0")] if a.b0 else []) + CASES
    for rep, (cls, phase, ordinal, pol) in enumerate(cases, start=1):
        goal_key = "s4_%s_%s_%s" % (cls, pol, uuid.uuid4().hex[:8])
        executor = AgenticExecutor(model_cfg)
        executor._raf_context = {"goal_key": goal_key, "temperature": 0.0, "seed": 0}
        executor.pre_attempt_hook = PreAttempt(executor, base_url, pol)
        inj = None if pol == "B0" else _injector(cls, phase, ordinal, goal_key)
        goal_id = execute_goal(
            db=db, exp_id=exp_id, hw_id=hw_id, harness=harness, executor=executor,
            task={**task, "meta": task.get("meta", {})}, workflow_type="agentic",
            rep_num=rep, goal_tracker=rx._goal_tracker, policy=policy,
            failure_injector=inj, repetitions=len(cases), retry_adapter=retry_adapter,
            quality_enabled=True)
        prep = getattr(executor, "_raf_prepare", {}) or {}
        print("goal=%s class=%s policy=%s prepare=%s" % (
            goal_id, cls, pol,
            {k: {"resets": [r["status"] for r in v["resets"]],
                 "restore_ok": (v["restore"] or {}).get("ok"),
                 "validity": v["validity"]} for k, v in prep.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
