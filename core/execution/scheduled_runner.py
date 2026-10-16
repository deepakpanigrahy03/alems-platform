"""
Scheduled profile runner: alems run <profile> for profiles with a schedule
section (RAF R2; coordinator decisions of 15 Oct, 13:00).

Entered from run_experiment.main after apply_config when the profile has a
schedule section; every other profile keeps the plain loop (Rule S).

Per goal: idle check, executor with the recovery prepare hook (plugin),
fire once injector, execute_goal, energy guard, validity classifier (plugin),
one rerun per invalid goal, ledger record. Once per run, before the first
goal: optional baseline remeasure. All of it happens between goal
executions, outside every measurement window (C-BND); nothing here runs
inside [t0, t1].

Plugins (entry points, INV-2), named in the profile:
    alems.harness.prepare    recovery.prepare     factory of the pre attempt hook
    alems.harness.deciders   recovery.arms.<arm>  decide(attempt, executor)
    alems.harness.validity   validity.classifier  classify(conn, goal_id, plan, prepare, meta)

Outputs under <store dir>/artifacts/<study.name>/:
    <study>_<run|smoke>_state.json    completed queue item ids (resume)
    <study>_<run|smoke>_ledger.jsonl  one record per goal execution
"""

import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from core.execution import schedule as sch
from core.telemetry import vllm_metrics

ATTEMPT_NUMBER_COL = "attempt_number"   # goal_attempt column holding 1, 2, 3

logger = logging.getLogger(__name__)

ENGINE = Path(__file__).resolve().parents[2]
GROUP_PREPARE = "alems.harness.prepare"
GROUP_DECIDER = "alems.harness.deciders"
GROUP_VALIDITY = "alems.harness.validity"


# ── plugins ──────────────────────────────────────────────────────────────
def load_plugin(group: str, name: str) -> Callable:
    """Load one entry point by group and name; fail loudly when absent."""
    from importlib.metadata import entry_points
    eps = entry_points()
    if hasattr(eps, "select"):
        found = list(eps.select(group=group, name=name))
    else:                                   # Python 3.9: dict of lists
        found = [e for e in eps.get(group, []) if e.name == name]
    if not found:
        raise LookupError("no plugin %r in entry point group %s" % (name, group))
    return found[0].load()


# ── provenance ───────────────────────────────────────────────────────────
def _git_state() -> Dict[str, Any]:
    def git(*a):
        return subprocess.run(["git", "-C", str(ENGINE)] + list(a),
                              capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ── isolation ────────────────────────────────────────────────────────────
def _cpu_times(source: str = "procstat") -> tuple:
    """(total, idle) CPU time counters; only the ratio of deltas is used."""
    if source == "psutil":
        # macOS has no /proc/stat; psutil reads host_statistics.
        import psutil
        t = psutil.cpu_times()
        return sum(t), t.idle + getattr(t, "iowait", 0.0)
    with open("/proc/stat") as fh:
        vals = [int(x) for x in fh.readline().split()[1:]]
    return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait


def _gpu_util() -> float:
    out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu",
                          "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout
    vals = [float(v) for v in out.split() if v.strip().replace(".", "").isdigit()]
    return max(vals) if vals else -1.0


def idle_check(iso: Dict[str, Any]) -> Dict[str, Any]:
    """Node CPU busy share and GPU utilization over a short window; no writes."""
    rec = {"tries": 0, "ok": False}
    for attempt in range(int(iso["retries"]) + 1):
        src = iso.get("cpu_source", "procstat")
        t0, i0 = _cpu_times(src)
        time.sleep(iso["idle_window_s"])
        t1, i1 = _cpu_times(src)
        busy = 100.0 * (1 - (i1 - i0) / max(1, t1 - t0))
        # gpu_check false (no nvidia-smi): recorded as None, never 0
        gpu = _gpu_util() if iso.get("gpu_check", True) else None
        gpu_ok = gpu is None or 0 <= gpu <= iso["gpu_util_max_pct"]
        rec.update({"tries": attempt + 1, "cpu_busy_pct": round(busy, 2),
                    "gpu_util_pct": gpu})
        if busy <= iso["cpu_busy_max_pct"] and gpu_ok:
            rec["ok"] = True
            return rec
        if attempt < int(iso["retries"]):
            time.sleep(iso["retry_wait_s"])
    return rec


def remeasure_baseline(harness, bcfg: Dict[str, Any]) -> Any:
    """Fresh idle baseline through the engine call the driver used (outside windows)."""
    engine = getattr(harness, "energy_engine", None) or getattr(harness, "engine", None)
    if engine is None or not hasattr(engine, "measure_idle_baseline"):
        raise RuntimeError("harness exposes no energy engine with measure_idle_baseline")
    kw = {k: bcfg[k] for k in ("duration_seconds", "num_samples", "pre_wait_seconds") if k in bcfg}
    b = engine.measure_idle_baseline(force_remeasure=True, **kw)
    if b is None:
        raise RuntimeError("baseline measurement returned None")
    # The harness reads the newest idle_baselines row (BaselineManager.get_latest),
    # so the new baseline is saved through the same manager.
    harness.baseline_mgr.save(b)
    return b.baseline_id


# ── runner ───────────────────────────────────────────────────────────────
def _cache_reset_path(provider: str) -> Optional[str]:
    """cache_reset.path of the provider in config/models.yaml, or None (vLLM default)."""
    with open(ENGINE / "config" / "models.yaml") as fh:
        prov = (yaml.safe_load(fh).get("providers") or {}).get(provider) or {}
    return (prov.get("cache_reset") or {}).get("path")


def _provider(profile: Dict[str, Any]) -> Dict[str, str]:
    """First providers entry: {provider, model}."""
    entries = profile.get("providers") or []
    if not entries:
        raise ValueError("scheduled profile needs a providers entry")
    p = entries[0]
    # Same keys as experiment_config_loader._apply_providers_section.
    name, model = p.get("name"), p.get("model_id")
    if not name or not model:
        raise ValueError("providers[0] needs name and model_id")
    return {"provider": name, "model": model}


class Runner:
    """Shared engine objects for one scheduled run (as the pilot driver built them)."""

    def __init__(self, args, profile: Dict[str, Any]):
        from core.config_loader import ConfigLoader
        from core.execution.experiment_runner import ExperimentRunner
        from core.execution.harness import ExperimentHarness
        from core.execution.frameworks.bootstrap import register_all_frameworks
        from core.execution.tests import run_experiment as rx
        from core.retry.retry_adapter import get_retry_adapter
        from core.utils.task_loader import load_tasks
        register_all_frameworks()       # harness never fills framework_registry
        self.profile, self.rx = profile, rx
        self.sched = profile["schedule"]
        self.study = profile["study"]
        prov = _provider(profile)
        self.provider = prov["provider"]
        # Same run arguments as the driver: agentic, failure injection, one retry.
        args.save_db, args.model, args.workflow_mode = True, prov["model"], "agentic"
        args.experiment_type = self.study.get("experiment_type", "failure_injection")
        args.experiment_goal = self.study.get("experiment_goal", self.study["name"])
        args.quality_enabled, args.max_retries = True, 1
        self.args = args
        self.harness = ExperimentHarness(ConfigLoader())
        self.runner = ExperimentRunner(getattr(self.harness, "config", None)
                                       or ConfigLoader(), args)
        self.tasks = {t["id"]: t for t in load_tasks()}
        self.model_cfg = self.runner.config.get_model_config_v2(self.provider, prov["model"])
        self.base_url = (self.model_cfg.get("base_url")
                         or self.model_cfg.get("api_endpoint") or "")
        self.reset_path = _cache_reset_path(self.provider)
        self.db, self.hw_id, self.env_id = self.runner.setup_database()
        self.conn = self.db.db.conn          # existing connection, reads only
        self.retry_adapter = get_retry_adapter(args)
        self.policy = dc_replace(
            rx._retry_coordinator.load_policy(self.conn, "default"),
            max_retries=1, backoff_seconds=0, retry_on_timeout=True,
            retry_on_tool_error=True, retry_on_api_error=True)
        rec = profile.get("recovery") or {}
        self.make_prepare = load_plugin(GROUP_PREPARE, rec.get("prepare", "bfcl"))
        self.deciders = {arm: load_plugin(GROUP_DECIDER, name)
                         for arm, name in (rec.get("arms") or {}).items()}
        val = profile.get("validity") or {}
        self.classify = load_plugin(GROUP_VALIDITY, val.get("classifier", "bfcl_raf"))
        self.exp_ids: Dict[str, int] = {}
        self.rep = 0

    def exp_id(self, task_id: str) -> int:
        """One experiment row per task per run, created lazily."""
        if task_id not in self.exp_ids:
            t = self.tasks[task_id]
            self.exp_ids[task_id] = self.runner.create_experiment(
                self.db, t["id"], t["name"], self.provider, self.model_cfg, "US", 1,
                self.hw_id, self.env_id, experiment_type=self.args.experiment_type,
                experiment_goal=self.args.experiment_goal, workflow_mode="agentic")
        return self.exp_ids[task_id]

    def run_goal(self, g: Dict[str, Any]) -> Dict[str, Any]:
        """execute_goal for one planned goal; returns ids, records, wall time."""
        from core.execution.agentic import AgenticExecutor
        from core.execution.goal_execution_manager import execute_goal
        from core.injection.scenario_injector import ScenarioInjector
        key = "%s_%s_%s_%s" % (self.study["name"], g["cls"], g["policy"], uuid.uuid4().hex[:8])
        ex = AgenticExecutor(self.model_cfg)
        ex._raf_context = {"goal_key": key, "temperature": 0.0,
                           "seed": self.sched["seeds"]["model_seed"]}
        if g.get("turn_check"):
            ex._raf_context["turn_check"] = True       # per turn state check
        decide = self.deciders.get(g.get("arm"))       # fixed arm: no decider
        ex.pre_attempt_hook = self.make_prepare(
            ex, self.base_url, g["policy"], reset_path=self.reset_path, decide=decide,
            metrics=vllm_metrics.read)                 # core supplies the reader (INV-14)
        inj = None
        if g["policy"] != "B0":
            rule = {"type": g["cls"], "rate": 1.0,
                    "max_injections": int(g.get("max_injections", 1)),
                    "location": {"phase": g["phase"], "step_index": g["ordinal"],
                                 "tool_name": g.get("tool_name") or "any"}}
            inj = ScenarioInjector([rule], scenario_id="raf_" + key,
                                   experiment_type="failure_injection")
        task = self.tasks[g["task"]]
        self.rep += 1
        t0 = time.time()
        goal_id = execute_goal(
            db=self.db, exp_id=self.exp_id(g["task"]), hw_id=self.hw_id,
            harness=self.harness, executor=ex,
            task={**task, "meta": task.get("meta", {})}, workflow_type="agentic",
            rep_num=self.rep, goal_tracker=self.rx._goal_tracker,
            policy=dc_replace(self.policy, max_retries=int(g.get("attempts", 2)) - 1),
            failure_injector=inj, repetitions=1, retry_adapter=self.retry_adapter,
            quality_enabled=True)
        after = vllm_metrics.read(self.base_url)       # after the goal, outside windows
        self._write_cache_snapshots(goal_id, getattr(ex, "_raf_prepare", {}) or {}, after)
        return {"goal_id": goal_id, "goal_key": key, "wall_s": time.time() - t0,
                "cache_metrics_after": after,
                "baseline_runs": self.baseline_used(goal_id),
                "prepare": getattr(ex, "_raf_prepare", {}) or {},
                "attempt_meta": dict(getattr(ex, "_raf_attempts", None) or {}),
                "decisions": dict(getattr(ex, "_raf_decisions", None) or {})}

    def _write_cache_snapshots(self, goal_id: int, prepare: Dict[int, Any],
                               after: Optional[Dict[str, float]]) -> None:
        """One cache_state_snapshots row per reading (before each attempt, after the goal)."""
        ids = {int(n): (a, r) for a, n, r in self.conn.execute(
            "SELECT attempt_id, " + ATTEMPT_NUMBER_COL + ", run_id FROM goal_attempt "
            "WHERE goal_id=?", (goal_id,)).fetchall()}
        rows = []
        for n in sorted(ids):
            vals = (prepare.get(n) or {}).get("cache_metrics")
            if vals:
                rows.append(dict(vllm_metrics.snapshot_row(vals),
                                 attempt_id=ids[n][0], run_id=ids[n][1]))
        if after and ids:
            last = max(ids)
            rows.append(dict(vllm_metrics.snapshot_row(after),
                             attempt_id=ids[last][0], run_id=ids[last][1]))
        if not rows:
            return
        try:
            with self.db.transaction():
                self.conn.executemany(
                    """INSERT INTO cache_state_snapshots
                       (run_id, attempt_id, timestamp_ns, engine_name, cache_type,
                        capacity_tokens, occupied_tokens, occupancy_fraction,
                        hit_rate_aggregate)
                       VALUES (:run_id, :attempt_id, :timestamp_ns, :engine_name,
                               :cache_type, :capacity_tokens, :occupied_tokens,
                               :occupancy_fraction, :hit_rate_aggregate)""", rows)
        except Exception as exc:
            logger.warning("cache snapshots for goal %s failed: %s", goal_id, exc)

    def refresh_harness(self) -> None:
        """Rebuild the harness so it loads the newest idle baseline."""
        from core.config_loader import ConfigLoader
        from core.execution.harness import ExperimentHarness
        self.harness = ExperimentHarness(ConfigLoader())

    def baseline_used(self, goal_id: int) -> List[str]:
        """Distinct runs.baseline_id of the goal's attempts (authoritative)."""
        rows = self.conn.execute(
            "SELECT DISTINCT r.baseline_id FROM goal_attempt ga JOIN runs r "
            "ON r.run_id = ga.run_id WHERE ga.goal_id = ?", (goal_id,)).fetchall()
        return sorted(x[0] for x in rows if x[0])

    def energy_ok(self, goal_id: int) -> bool:
        """Every attempt run must carry nonzero package energy (fallback guard)."""
        rows = self.conn.execute(
            "SELECT r.pkg_energy_uj FROM goal_attempt ga JOIN runs r "
            "ON r.run_id=ga.run_id WHERE ga.goal_id=?", (goal_id,)).fetchall()
        return bool(rows) and all(r[0] for r in rows)


# ── queue execution ──────────────────────────────────────────────────────
def smoke_queue(queue: List[Dict[str, Any]], tasks: Dict[str, Any]) -> List[Dict[str, Any]]:
    """First B0, then the first block of each family (or stratum without families)."""
    out, seen = [], set()
    for item in queue:
        if item["kind"] == "b0":
            tag = "b0"
        else:
            tag = item.get("family") or tasks[item["task"]]["stratum"]
        if tag not in seen:
            seen.add(tag)
            out.append(item)
    return out


def select_items(queue: List[Dict[str, Any]], path: str) -> List[Dict[str, Any]]:
    """
    Queue items named in a file, one per line: item id, optionally a tab and
    one policy (then only that goal of the block runs). Lines starting with
    # are comments. Every named item must exist in the queue.
    """
    want: Dict[str, set] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            item, _, pol = line.partition("\t")
            want.setdefault(item, set())
            if pol:
                want[item].add(pol)
    out = []
    for it in queue:
        if it["id"] in want:
            pols = want[it["id"]]
            goals = [g for g in it["goals"] if not pols or g["policy"] in pols]
            out.append(dict(it, goals=goals))
    missing = set(want) - {i["id"] for i in out}
    if missing:
        raise SystemExit("items not in the queue: %s" % sorted(missing))
    return out


def _append(path: str, rec: Dict[str, Any]) -> None:
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def run_item(r: Runner, item: Dict[str, Any], ledger: str, common: Dict[str, Any]) -> list:
    """One block or B0 item: idle check, goals, validity, reruns."""
    iso = r.profile["isolation"]
    reruns = int((r.profile.get("validity") or {}).get("reruns", 1))
    idle = idle_check(iso)
    walls = []
    for idx, g in enumerate(item["goals"]):
        for rerun in range(reruns + 1):
            res = r.run_goal(g)
            if not r.energy_ok(res["goal_id"]):
                raise SystemExit("goal %s has zero or NULL energy: stop (fallback reader?)"
                                 % res["goal_id"])
            if common.get("baseline_id") and res["baseline_runs"] != [common["baseline_id"]]:
                raise SystemExit("goal %s runs used baseline %s, run measured %s: stop"
                                 % (res["goal_id"], res["baseline_runs"], common["baseline_id"]))
            val = r.classify(r.conn, res["goal_id"], g, res["prepare"], res["attempt_meta"])
            walls.append((r.sched["tasks"][g["task"]]["stratum"], res["wall_s"]))
            rec = {"item_id": item["id"], "position": item["position"], "goal_index": idx,
                   "plan": g, "seeds": r.sched["seeds"],
                   "policy_order": [x["policy"] for x in item["goals"]],
                   "idle_check": idle, "isolation_level": iso["level"],
                   "rerun": rerun, "goal_id": res["goal_id"], "goal_key": res["goal_key"],
                   "validity": val["codes"], "detail": val["detail"],
                   "family": g.get("family"), "arm": g.get("arm"),
                   "attempt_meta": res["attempt_meta"], "decisions": res["decisions"],
                   "cache_metrics_after": res["cache_metrics_after"],
                   "baseline_runs": res["baseline_runs"],
                   "prepare": res["prepare"], "wall_s": round(res["wall_s"], 2)}
            rec.update(common)
            _append(ledger, rec)
            print("%s %s %s goal=%s valid=%s %.1fs" % (
                item["id"], g["policy"], "rerun" if rerun else "", res["goal_id"],
                val["codes"] or "OK", res["wall_s"]))
            if not val["codes"]:
                break
    return walls


def project(walls: list, r: Runner, full: List[Dict[str, Any]]) -> None:
    """Projected wall time of the full queue from per stratum means."""
    by: Dict[str, List[float]] = {}
    for stratum, w in walls:
        by.setdefault(stratum, []).append(w)
    mean = {k: statistics.mean(v) for k, v in by.items()}
    total = overhead = 0.0
    for item in full:
        st = r.sched["tasks"][item["task"]]["stratum"]
        total += len(item["goals"]) * mean.get(st, max(mean.values()))
        overhead += r.profile["isolation"]["idle_window_s"]
    print("mean wall s per goal by stratum:", {k: round(v, 1) for k, v in mean.items()})
    print("projected hours: goals %.1f, idle checks %.1f, total %.1f" % (
        total / 3600, overhead / 3600, (total + overhead) / 3600))


def _store_path() -> str:
    sys.path.insert(0, str(ENGINE / "scripts" / "tools"))
    from path_loader import get_alems_db_path   # store resolver (design 7.7)
    return str(get_alems_db_path())


def run_scheduled(args) -> int:
    """Entry from run_experiment.main for a profile with a schedule section."""
    profile = args.profile_cfg
    sched, study = profile["schedule"], profile["study"]
    store = _store_path()
    suffix = sched.get("expected_store_suffix")
    if suffix and not store.endswith(suffix):
        print("error: store %s is not the expected store (%s); cd into the sandbox"
              % (store, suffix), file=sys.stderr)
        return 3
    # Machine config is read relative to the working directory (E5): pin the
    # resolved store for this process, then run from the engine root.
    os.environ["ALEMS_STORE"] = store
    os.chdir(str(ENGINE))
    if _store_path() != store:
        print("error: store changed after chdir: %s" % _store_path(), file=sys.stderr)
        return 3
    out_dir = os.path.join(os.path.dirname(store), "artifacts", study["name"])
    os.makedirs(out_dir, exist_ok=True)
    r = Runner(args, profile)
    common = {"study": study["name"], "profile_sha256": _sha256(args.config),
              "engine": _git_state(), "argv": list(sys.argv), "baseline_id": None}
    bcfg = profile.get("baseline") or {}
    if bcfg.get("remeasure"):
        common["baseline_id"] = remeasure_baseline(r.harness, bcfg)
        # The harness loads the newest baseline once, when built; rebuild it so
        # the runs use the baseline just measured (defect found in E1, Oct 15).
        r.refresh_harness()
        print("baseline saved", common["baseline_id"])
    families = [f for f in (getattr(args, "families", "") or "").split(",") if f] or None
    full = sch.build_queue(sched, families)
    smoke = bool(getattr(args, "smoke", False))
    items_file = getattr(args, "items", "") or ""
    if items_file:
        # Reruns: own state and ledger, never the study's run ledger.
        queue = select_items(full, items_file)
        kind = "items_" + os.path.splitext(os.path.basename(items_file))[0]
    else:
        queue = smoke_queue(full, sched["tasks"]) if smoke else full
        kind = "smoke" if smoke else "run"
    tag = "%s_%s" % (study["name"], kind)
    state_path = os.path.join(out_dir, "%s_state.json" % tag)
    ledger = os.path.join(out_dir, "%s_ledger.jsonl" % tag)
    done = set(json.load(open(state_path))) if os.path.exists(state_path) else set()
    print("store %s queue %d items %s done %d" % (store, len(queue), sch.count_goals(queue), len(done)))
    max_items = int(getattr(args, "max_items", 0) or 0)
    walls, ran = [], 0
    for item in queue:
        if item["id"] in done:
            continue
        walls += run_item(r, item, ledger, common)
        done.add(item["id"])
        with open(state_path, "w") as fh:
            json.dump(sorted(done), fh)
        ran += 1
        if max_items and ran >= max_items:
            break
    if walls:
        project(walls, r, full)
    return 0
