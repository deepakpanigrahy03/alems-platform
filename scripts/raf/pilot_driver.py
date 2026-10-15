#!/usr/bin/env python3
"""
RAF LH 01 pilot driver (SPEC S5, design v2 FINAL section 10). GN100 only.

Run from the pilot sandbox directory; the store resolves from the sandbox:
    cd ~/sandboxes/raf-mlsys-2027
    python3 ~/mydrive/alems-platform/scripts/raf/pilot_driver.py \
        --provider <p> --model <m> [--smoke | --max-items N] [--points early]

Built on s4_smoke.py: same construction, injector rule, PreAttempt hook and
execute_goal call. Adds the queue (pilot_schedule), an idle check before each
block, validity classification with one rerun (pilot_validity), an energy
guard, a resumable state file, a JSONL ledger per goal execution, and the
time projection. Everything the driver itself does happens between goal
executions, outside every measurement window (C-BND).

Outputs, next to the sandbox store under artifacts/raf_pilot/:
    state.json     completed queue item ids (resume)
    ledger.jsonl   one record per goal execution (plan, seeds, order,
                   idle check, validity codes, rerun flag, wall time)
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import replace as dc_replace

import yaml

ENGINE = os.path.expanduser("~/mydrive/alems-platform")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
sys.path.insert(0, HERE)

import pilot_schedule as ps  # noqa: E402
import pilot_validity as pv  # noqa: E402


# ── setup ───────────────────────────────────────────────────────────────
def _args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--config", default=os.path.join(HERE, "pilot_gn100.yaml"))
    ap.add_argument("--smoke", action="store_true",
                    help="first short block, first long block, one B0; separate state")
    ap.add_argument("--max-items", type=int, default=0, help="stop after N queue items")
    ap.add_argument("--points", default="early,late", help="cut: early only")
    ap.add_argument("--classes", default="", help="cut: comma list of classes to keep")
    ap.add_argument("--remeasure-baseline", action="store_true",
                    help="measure a fresh idle baseline and exit")
    return ap.parse_args()


def _load_cfg(a: argparse.Namespace) -> dict:
    with open(a.config) as fh:
        cfg = yaml.safe_load(fh)
    cfg["points"] = [p for p in a.points.split(",") if p]
    if a.classes:
        keep = set(a.classes.split(","))
        cfg["classes"] = {c: v for c, v in cfg["classes"].items() if c in keep}
    return cfg


def _store_path() -> str:
    sys.path.insert(0, os.path.join(ENGINE, "scripts", "tools"))
    from path_loader import get_alems_db_path
    return get_alems_db_path()


def _guard_store(cfg: dict) -> str:
    """Refuse to run against any store but the pilot sandbox store."""
    path = _store_path()
    if not str(path).endswith(cfg["expected_store_suffix"]):
        sys.exit("store %s is not the pilot store (%s); cd into the sandbox"
                 % (path, cfg["expected_store_suffix"]))
    return str(path)


def _run_args(model: str):
    """run_experiment's argument namespace with defaults, then pilot values."""
    from core.execution.tests import run_experiment as rx
    saved, sys.argv = sys.argv, [sys.argv[0]]
    try:
        args = rx.parse_arguments()
    finally:
        sys.argv = saved
    args.save_db, args.model, args.workflow_mode = True, model, "agentic"
    args.experiment_type = "failure_injection"
    args.experiment_goal = "RAF LH 01 pilot"
    args.quality_enabled, args.max_retries = True, 1
    return rx, args


# ── isolation ───────────────────────────────────────────────────────────
def _cpu_times(source: str = "procstat") -> tuple:
    """(total, idle) CPU time counters; only the ratio of deltas is used."""
    if source == "psutil":
        # macOS has no /proc/stat; psutil reads host_statistics. Same busy
        # definition as below: idle plus iowait (absent on macOS) over all.
        import psutil
        t = psutil.cpu_times()
        return sum(t), t.idle + getattr(t, "iowait", 0.0)
    with open("/proc/stat") as fh:
        vals = [int(x) for x in fh.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
    return sum(vals), idle


def _gpu_util() -> float:
    out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu",
                          "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout
    vals = [float(v) for v in out.split() if v.strip().replace(".", "").isdigit()]
    return max(vals) if vals else -1.0


def idle_check(iso: dict) -> dict:
    """Node CPU busy share and GPU utilization over a short window; no writes."""
    rec = {"tries": 0, "ok": False}
    for attempt in range(iso["retries"] + 1):
        src = iso.get("cpu_source", "procstat")
        t0, i0 = _cpu_times(src)
        time.sleep(iso["idle_window_s"])
        t1, i1 = _cpu_times(src)
        busy = 100.0 * (1 - (i1 - i0) / max(1, t1 - t0))
        # gpu_check false (macOS, no nvidia-smi): recorded as None, never 0
        gpu = _gpu_util() if iso.get("gpu_check", True) else None
        gpu_ok = gpu is None or 0 <= gpu <= iso["gpu_util_max_pct"]
        rec.update({"tries": attempt + 1, "cpu_busy_pct": round(busy, 2),
                    "gpu_util_pct": gpu})
        if busy <= iso["cpu_busy_max_pct"] and gpu_ok:
            rec["ok"] = True
            return rec
        if attempt < iso["retries"]:
            time.sleep(iso["retry_wait_s"])
    return rec


def remeasure_baseline(harness, bcfg: dict) -> None:
    """Fresh idle baseline through the existing engine call (outside windows)."""
    engine = getattr(harness, "energy_engine", None) or getattr(harness, "engine", None)
    if engine is None or not hasattr(engine, "measure_idle_baseline"):
        sys.exit("harness exposes no energy engine with measure_idle_baseline")
    b = engine.measure_idle_baseline(force_remeasure=True, **bcfg)
    if b is None:
        sys.exit("baseline measurement returned None")
    # The harness reads the newest idle_baselines row (BaselineManager.get_latest),
    # so the new baseline must be saved through the same manager.
    harness.baseline_mgr.save(b)
    print("baseline saved", b.baseline_id, b.sample_count)


# ── one goal execution ──────────────────────────────────────────────────
def _cache_reset_path(provider: str):
    """cache_reset.path of the provider in config/models.yaml, or None.

    get_model_config_v2 does not carry provider level extras, so the raw
    entry is read here; absent key means the vLLM default route.
    """
    with open(os.path.join(ENGINE, "config", "models.yaml")) as fh:
        prov = (yaml.safe_load(fh).get("providers") or {}).get(provider) or {}
    return (prov.get("cache_reset") or {}).get("path")


class Pilot:
    """Holds the shared runner objects for one driver session."""

    def __init__(self, a, cfg, out_dir):
        self.rx, self.args = _run_args(a.model)
        from core.config_loader import ConfigLoader
        from core.execution.experiment_runner import ExperimentRunner
        from core.execution.harness import ExperimentHarness
        from core.execution.frameworks.bootstrap import register_all_frameworks
        from core.retry.retry_adapter import get_retry_adapter
        from core.utils.task_loader import load_tasks
        register_all_frameworks()   # harness never fills framework_registry (S4 G)
        self.cfg, self.a, self.out_dir = cfg, a, out_dir
        self.harness = ExperimentHarness(ConfigLoader())
        self.runner = ExperimentRunner(self.harness.config if hasattr(
            self.harness, "config") else ConfigLoader(), self.args)
        self.tasks = {t["id"]: t for t in load_tasks()}
        self.model_cfg = self.runner.config.get_model_config_v2(a.provider, a.model)
        self.base_url = (self.model_cfg.get("base_url")
                         or self.model_cfg.get("api_endpoint") or "")
        self.reset_path = _cache_reset_path(a.provider)
        self.db, self.hw_id, self.env_id = self.runner.setup_database()
        self.conn = self.db.db.conn          # existing connection, reads only
        self.retry_adapter = get_retry_adapter(self.args)
        self.policy = dc_replace(
            self.rx._retry_coordinator.load_policy(self.conn, "default"),
            max_retries=1, backoff_seconds=0, retry_on_timeout=True,
            retry_on_tool_error=True, retry_on_api_error=True)
        self.exp_ids = {}
        self.rep = 0

    def exp_id(self, task_id: str) -> int:
        """One experiment row per task per session, created lazily."""
        if task_id not in self.exp_ids:
            t = self.tasks[task_id]
            self.exp_ids[task_id] = self.runner.create_experiment(
                self.db, t["id"], t["name"], self.a.provider, self.model_cfg, "US", 1,
                self.hw_id, self.env_id, experiment_type="failure_injection",
                experiment_goal="RAF LH 01 pilot", workflow_mode="agentic")
        return self.exp_ids[task_id]

    def run_goal(self, g: dict) -> dict:
        """execute_goal for one planned goal; returns ids, prepare, wall time."""
        from alems_plugin_bfcl.prepare import PreAttempt
        from core.execution.agentic import AgenticExecutor
        from core.execution.goal_execution_manager import execute_goal
        from core.injection.scenario_injector import ScenarioInjector
        key = "pilot_%s_%s_%s" % (g["cls"], g["policy"], uuid.uuid4().hex[:8])
        ex = AgenticExecutor(self.model_cfg)
        ex._raf_context = {"goal_key": key, "temperature": 0.0,
                           "seed": self.cfg["seeds"]["model_seed"]}
        ex.pre_attempt_hook = PreAttempt(
            ex, self.base_url, g["policy"], reset_path=self.reset_path)
        inj = None
        if g["policy"] != "B0":
            rule = {"type": g["cls"], "rate": 1.0, "max_injections": 1,
                    "location": {"phase": g["phase"], "step_index": g["ordinal"],
                                 "tool_name": "any"}}
            inj = ScenarioInjector([rule], scenario_id="raf_" + key,
                                   experiment_type="failure_injection")
        task = self.tasks[g["task"]]
        self.rep += 1
        t0 = time.time()
        goal_id = execute_goal(
            db=self.db, exp_id=self.exp_id(g["task"]), hw_id=self.hw_id,
            harness=self.harness, executor=ex,
            task={**task, "meta": task.get("meta", {})}, workflow_type="agentic",
            rep_num=self.rep, goal_tracker=self.rx._goal_tracker, policy=self.policy,
            failure_injector=inj, repetitions=1, retry_adapter=self.retry_adapter,
            quality_enabled=True)
        return {"goal_id": goal_id, "goal_key": key, "wall_s": time.time() - t0,
                "prepare": getattr(ex, "_raf_prepare", {}) or {}}

    def energy_ok(self, goal_id: int) -> bool:
        """Every attempt run must carry nonzero package energy (fallback guard)."""
        rows = self.conn.execute(
            "SELECT r.pkg_energy_uj FROM goal_attempt ga JOIN runs r "
            "ON r.run_id=ga.run_id WHERE ga.goal_id=?", (goal_id,)).fetchall()
        return bool(rows) and all(r[0] for r in rows)


# ── queue execution ─────────────────────────────────────────────────────
def _smoke_queue(queue: list, cfg: dict) -> list:
    """First block of a short task, first of a long task, first B0."""
    out, seen = [], set()
    for item in queue:
        tag = item["kind"] if item["kind"] == "b0" else cfg["tasks"][item["task"]]["stratum"]
        if tag not in seen:
            seen.add(tag)
            out.append(item)
    return out


def _append(path: str, rec: dict) -> None:
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def run_item(p: Pilot, item: dict, ledger: str) -> list:
    """One block or B0 item: idle check, goals, validity, one rerun each."""
    idle = idle_check(p.cfg["isolation"])
    walls = []
    for idx, g in enumerate(item["goals"]):
        for rerun in range(p.cfg["validity"]["reruns"] + 1):
            res = p.run_goal(g)
            if not p.energy_ok(res["goal_id"]):
                sys.exit("goal %s has zero or NULL energy: stop (fallback reader?)"
                         % res["goal_id"])
            val = pv.classify(p.conn, res["goal_id"], g, res["prepare"])
            walls.append((p.cfg["tasks"][g["task"]]["stratum"], res["wall_s"]))
            _append(ledger, {
                "item_id": item["id"], "position": item["position"], "goal_index": idx,
                "plan": g, "seeds": p.cfg["seeds"],
                "policy_order": [x["policy"] for x in item["goals"]],
                "idle_check": idle, "isolation_level": p.cfg["isolation"]["level"],
                "rerun": rerun, "goal_id": res["goal_id"], "goal_key": res["goal_key"],
                "validity": val["codes"], "detail": val["detail"],
                "prepare": res["prepare"], "wall_s": round(res["wall_s"], 2)})
            print("%s %s %s goal=%s valid=%s %.1fs" % (
                item["id"], g["policy"], "rerun" if rerun else "", res["goal_id"],
                val["codes"] or "OK", res["wall_s"]))
            if not val["codes"]:
                break
    return walls


def project(walls: list, cfg: dict, full_queue: list) -> None:
    """Projected wall time of the full queue from per stratum means."""
    by = {}
    for stratum, w in walls:
        by.setdefault(stratum, []).append(w)
    mean = {k: statistics.mean(v) for k, v in by.items()}
    total, overhead = 0.0, 0.0
    for item in full_queue:
        st = cfg["tasks"][item["task"]]["stratum"]
        total += len(item["goals"]) * mean.get(st, max(mean.values()))
        overhead += cfg["isolation"]["idle_window_s"]
    print("mean wall s per goal by stratum:", {k: round(v, 1) for k, v in mean.items()})
    print("projected hours: goals %.1f, idle checks %.1f, total %.1f" % (
        total / 3600, overhead / 3600, (total + overhead) / 3600))


def main() -> int:
    a = _args()
    cfg = _load_cfg(a)
    store = _guard_store(cfg)
    # E5: machine config is read relative to the working directory, so the
    # engine must run from its root. The store was resolved from the sandbox
    # above; pin it for this process only, then move to the engine root.
    os.environ["ALEMS_STORE"] = store
    os.chdir(ENGINE)
    if _store_path() != store:
        sys.exit("store changed after chdir: %s" % _store_path())
    out_dir = os.path.join(os.path.dirname(store), "artifacts", cfg.get("pilot", "raf_pilot"))
    os.makedirs(out_dir, exist_ok=True)
    p = Pilot(a, cfg, out_dir)
    if a.remeasure_baseline:
        remeasure_baseline(p.harness, cfg["baseline"])
        return 0
    full = ps.build_queue(cfg)
    queue = _smoke_queue(full, cfg) if a.smoke else full
    tag = "smoke" if a.smoke else "pilot"
    state_path = os.path.join(out_dir, "%s_state.json" % tag)
    ledger = os.path.join(out_dir, "%s_ledger.jsonl" % tag)
    done = set(json.load(open(state_path))) if os.path.exists(state_path) else set()
    print("store %s queue %d items %s done %d" % (store, len(queue), ps.count_goals(queue), len(done)))
    walls, ran = [], 0
    for item in queue:
        if item["id"] in done:
            continue
        walls += run_item(p, item, ledger)
        done.add(item["id"])
        with open(state_path, "w") as fh:
            json.dump(sorted(done), fh)
        ran += 1
        if a.max_items and ran >= a.max_items:
            break
    if walls:
        project(walls, cfg, full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
