"""
RAF R1 (S8) smoke: one goal per new failure class plus one B0 with checks.

Throwaway acceptance driver for SPEC_RAF_R1 section 6 item 2. It reuses
pilot_driver unchanged (store guard, idle check, energy guard, ledger,
validity loop) and patches three things for this process only:

    _smoke_queue   returns the R1 items below
    Pilot.run_goal R1 rules (persistent: max_injections 2, three attempts),
                   turn_check on, per attempt records captured
    pv.classify    receives the per attempt records (attempt_meta)

R2 extends pilot_driver properly; this file is then retired.

Usage (from the sandbox directory, between run batches only):
    python3 $HOME/mydrive/alems-platform/scripts/raf/s8_smoke.py \
        --provider vllm_local --model Qwen3-4B-Instruct-2507
"""

import os
import sys
import time
import uuid
from dataclasses import replace as dc_replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pilot_driver as pd  # noqa: E402

# Edit tasks or ordinals here if a class shows DETECTION_MISSED (plan D4:
# silent on a read whose output feeds a later write in the same turn).
TASK = "bfcl_multi_turn_base_7"
R1_GOALS = [
    {"cls": "silent_tool_output",    "phase": "tool", "ordinal": "any",
     "tool_name": "post_tweet",
     "task": "bfcl_multi_turn_base_175"},   # post_tweet id feeds retweet
    {"cls": "wrong_model_step",      "phase": "tool", "ordinal": 2},
    {"cls": "persistent_rate_limit", "phase": "llm",  "ordinal": 2},
    {"cls": "persistent_tool_error", "phase": "tool", "ordinal": 2},
]
CONTAMINATING = ("silent_tool_output", "wrong_model_step")

_last_meta = {}   # attempt records of the goal just run, read by classify


def _queue(_full, _cfg):
    """R1 items: one goal per class (FR warm), then one B0 with checks."""
    items = []
    for i, g in enumerate(R1_GOALS):
        task = g.get("task", TASK)
        items.append({"id": "r1_smoke_%d_%s" % (i, task[-3:]), "position": i,
                      "kind": "block", "task": task,
                      "goals": [dict(g, task=task, policy="FR_warm")]})
    for task in (TASK, "bfcl_multi_turn_base_175"):
        items.append({"id": "r1_smoke_b0_%s" % task[-3:], "position": len(items),
                      "kind": "b0", "task": task,
                      "goals": [{"cls": None, "phase": None, "ordinal": None,
                                 "task": task, "policy": "B0"}]})
    return items


def _run_goal(self, g):
    """pilot run_goal with the R1 differences marked."""
    from alems_plugin_bfcl.prepare import PreAttempt
    from core.execution.agentic import AgenticExecutor
    from core.execution.goal_execution_manager import execute_goal
    from core.injection.scenario_injector import ScenarioInjector
    global _last_meta
    key = "r1smoke_%s_%s_%s" % (g["cls"], g["policy"], uuid.uuid4().hex[:8])
    ex = AgenticExecutor(self.model_cfg)
    persistent = str(g["cls"]).startswith("persistent_")
    ex._raf_context = {"goal_key": key, "temperature": 0.0,
                       "seed": self.cfg["seeds"]["model_seed"],
                       # R1: checks for contaminating goals and their B0
                       "turn_check": g["policy"] == "B0" or g["cls"] in CONTAMINATING}
    ex.pre_attempt_hook = PreAttempt(ex, self.base_url, g["policy"],
                                     reset_path=self.reset_path)
    inj = None
    if g["policy"] != "B0":
        rule = {"type": g["cls"], "rate": 1.0,
                "max_injections": 2 if persistent else 1,   # R1: recur once
                "location": {"phase": g["phase"], "step_index": g["ordinal"],
                             "tool_name": g.get("tool_name", "any")}}
        inj = ScenarioInjector([rule], scenario_id="raf_" + key,
                               experiment_type="failure_injection")
    # R1: persistent needs a third attempt, otherwise no option can succeed.
    policy = dc_replace(self.policy, max_retries=2) if persistent else self.policy
    task = self.tasks[g["task"]]
    self.rep += 1
    t0 = time.time()
    goal_id = execute_goal(
        db=self.db, exp_id=self.exp_id(g["task"]), hw_id=self.hw_id,
        harness=self.harness, executor=ex,
        task={**task, "meta": task.get("meta", {})}, workflow_type="agentic",
        rep_num=self.rep, goal_tracker=self.rx._goal_tracker, policy=policy,
        failure_injector=inj, repetitions=1, retry_adapter=self.retry_adapter,
        quality_enabled=True)
    _last_meta = getattr(ex, "_raf_attempts", {}) or {}
    return {"goal_id": goal_id, "goal_key": key, "wall_s": time.time() - t0,
            "prepare": getattr(ex, "_raf_prepare", {}) or {}}


_classify = pd.pv.classify


def _classify_r1(conn, goal_id, plan, prepare=None):
    """Original classify plus the attempt records; prints the check summary."""
    out = _classify(conn, goal_id, plan, prepare, attempt_meta=_last_meta)
    for a, m in sorted(_last_meta.items()):
        checks = m.get("turn_checks") or []
        print("  attempt %s corruption=%s checks=%s ms=%.1f" % (
            a, m.get("corruption"), [(c["turn"], c["ok"]) for c in checks],
            sum(c["ms"] for c in checks)))
    return out


if __name__ == "__main__":
    pd._smoke_queue = _queue
    pd.Pilot.run_goal = _run_goal
    pd.pv.classify = _classify_r1
    if "--smoke" not in sys.argv:
        sys.argv.append("--smoke")
    sys.exit(pd.main())
