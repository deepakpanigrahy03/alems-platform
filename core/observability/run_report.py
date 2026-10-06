"""
Run header and run card (user output, 39.5.2e).

header(): before the first measurement. What runs and where its records go:
engine and git state, invocation (sandbox or engine root), profile, lock file,
configuration directory with its origin and files, store and database mode,
log and error directories, models with adapters, tasks, repetitions, country,
baseline, readers with fidelity.

footer(): after persistence, one card per run read back from the store, so it
shows what was actually written: experiment, environment, path taken
(save_pair, save_single, execute_goal), attempt and outcome, energy, LLM,
tools, quality, spans, attribution, residual, injection and failures, detail
sections (timing, energy domains, phases, gpu, cpu, thermal, system, network,
orchestration, sustainability), stages and reasons, validity, error records,
log file, and rows written per table.

Rules: called only outside measurement windows; read only connection (legal
under EEI-2); every lookup is best effort and never raises, so reporting can
never fail a run; NULL values are omitted, never shown as 0; color only on a
terminal; --json gives one object.
"""

import getpass
import hashlib
import logging
import os
import socket
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.observability.console import get_console

logger = logging.getLogger(__name__)

# exp_id high water mark taken by header(); footer() reports experiments above it.
_state = {"exp_floor": None, "store": None}  # type: Dict[str, Any]

# One hue per family so a card can be scanned by color.
_COLORS = {
    "config": "blue", "store": "blue", "models": "magenta", "readers": "cyan",
    "energy": "orange", "timing": "orange", "energy domains": "orange", "phases": "orange",
    "attribution": "orange", "gpu": "green", "cpu": "blue", "thermal": "red",
    "system": "cyan", "network": "cyan", "orchestration": "magenta", "sustainability": "green",
    "llm": "magenta", "tools": "magenta", "quality": "green", "spans": "blue",
    "injection": "red", "failures": "red", "stages": "cyan", "valid": "bold",
    "errors": "red", "files": "blue", "rows written": "blue",
}
_KEY_WIDTH = 18


def _safe(fn, default=None):
    """Call fn; on any error log at debug and return default (never raise)."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  reporting is subordinate
        logger.debug("run report lookup failed: %s", e)
        return default


def _kv(con, key, value, color=None, indent=1):
    """Aligned line; the key is padded before styling so ANSI codes never shift columns."""
    if value in (None, "", [], {}):
        return
    padded = "%-*s" % (_KEY_WIDTH, key)
    con.line(con.style(padded, color or _COLORS.get(key, "")) + " " + str(value), indent)


def _store_path():
    # type: () -> Optional[str]
    # Core never imports scripts (CH39-3): use the core store resolver.
    from core.observability import locations
    found = locations.store_path()
    return str(found) if found else None


def _ro(path):
    # type: (str) -> sqlite3.Connection
    """Read only connection (no writer involvement, no lock taken)."""
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _engine_version():
    # type: () -> str
    from core.observability.setup import _engine_version as _ev  # private, best effort
    return str(_ev())


def _git():
    # type: () -> str
    """Engine git commit and dirty flag (paper provenance needs a clean tree)."""
    root = str(Path(__file__).resolve().parents[2])
    commit = subprocess.check_output(["git", "-C", root, "rev-parse", "--short", "HEAD"], text=True).strip()
    dirty = subprocess.call(["git", "-C", root, "diff", "--quiet"]) != 0
    return "%s (%s)" % (commit, "dirty" if dirty else "clean")


def _sandbox_root():
    # type: () -> Optional[Path]
    here = Path.cwd()
    for d in [here] + list(here.parents):
        if (d / "alems-sandbox.yaml").exists():
            return d
    return None


def _invocation(sroot, store):
    # type: (Optional[Path], Optional[str]) -> str
    """Sandbox from the cwd manifest, else from the store path (<data_root>/<host>/sandboxes/<name>/)."""
    if sroot is not None:
        return "sandbox %s (%s)" % (sroot.name, sroot)
    if store and Path(store).parent.parent.name == "sandboxes":
        return "sandbox %s (store %s)" % (Path(store).parent.name, Path(store).parent)
    return "engine root (%s)" % Path.cwd()


def _lock_digest(root):
    # type: (Optional[Path]) -> Optional[str]
    """SHA-256 of the sandbox alems.lock file (file digest, not the canonical lock hash)."""
    if root is None or not (root / "alems.lock").exists():
        return None
    return hashlib.sha256((root / "alems.lock").read_bytes()).hexdigest()[:12]


def _config_files(config):
    # type: (Any) -> Optional[str]
    """Config directory used by the loader, its origin, and the files present."""
    cdir = Path(getattr(config, "config_dir", ""))
    if not cdir.is_dir():
        return None
    origin = "sandbox" if (cdir.parent / "alems-sandbox.yaml").exists() else "engine template"
    names = sorted(p.name for p in cdir.iterdir() if p.suffix in (".json", ".yaml", ".yml"))
    return "%s (%s): %s" % (cdir, origin, ", ".join(names))


def _dirs():
    # type: () -> Dict[str, Optional[str]]
    from core.observability import locations as loc
    out = {}
    for key, name in (("log", "host_log_dir"), ("error", "error_dir")):
        fn = getattr(loc, name, None)
        out[key] = _safe(lambda fn=fn: str(fn())) if fn else None
    return out


def _fidelity(obj):
    # type: (Any) -> str
    return "LIMITED" if type(obj).__name__.startswith(("Dummy", "Fallback")) else "MEASURED"


def _readers(harness):
    # type: (Any) -> List[Tuple[str, str]]
    """(reader, fidelity) pairs from the harness energy engine."""
    eng = getattr(harness, "energy_engine", None)
    out = []
    for attr in ("energy_reader", "perf", "msr", "sensor", "turbostat", "scheduler", "disk_reader"):
        obj = getattr(eng, attr, None) if eng is not None else None
        if obj is not None:
            out.append(("%-8s %s" % (attr.replace("_reader", ""), type(obj).__name__), _fidelity(obj)))
    backend = getattr(getattr(eng, "gpu_collector", None), "backend", None)
    if backend is not None:
        ok = _safe(backend.is_available, False)
        out.append(("%-8s %s" % ("gpu", type(backend).__name__), "MEASURED" if ok else "LIMITED"))
    return out


def _adapter(config, provider, model_id):
    # type: (Any, str, Optional[str]) -> str
    cfg = config.get_model_config_v2(provider, model_id) or {}
    if cfg.get("openai_compat"):
        return "openai_compat"
    return str(cfg.get("adapter") or cfg.get("provider_type") or provider)


def header(harness=None, config=None, providers=None, tasks=None, repetitions=None,
           cool_down=None, country=None, model=None, profile=None):
    # type: (...) -> None
    """Render the run header; records the exp_id high water mark for footer()."""
    con = get_console()
    store = _safe(_store_path)
    _state["store"] = store
    _state["exp_floor"] = _safe(lambda: _ro(store).execute(
        "SELECT COALESCE(MAX(exp_id), 0) FROM experiments").fetchone()[0], 0) if store else 0
    sroot = _safe(_sandbox_root)
    dirs = _safe(_dirs, {}) or {}
    models = []
    for p in providers or []:
        mid = model or _safe(lambda p=p: config.list_models(p)[0]["model_id"])
        models.append("%s %s (adapter %s)" % (p, mid, _safe(lambda p=p, mid=mid: _adapter(config, p, mid), "?")))
    readers = _safe(lambda: _readers(harness), []) if harness is not None else []
    info = {
        "engine": "%s  git %s" % (_safe(_engine_version, "?"), _safe(_git, "?")),
        "host": "%s  user %s" % (socket.gethostname(), _safe(getpass.getuser, "?")),
        "invocation": _invocation(sroot, store),
        "profile": profile,
        "lock file": _safe(lambda: _lock_digest(sroot)),
        "config": _safe(lambda: _config_files(config)) if config is not None else None,
        "store": store,
        "database": ("sqlite, journal %s" % _safe(lambda: _ro(store).execute("PRAGMA journal_mode").fetchone()[0], "?")) if store else None,
        "log dir": dirs.get("log"),
        "error dir": dirs.get("error"),
        "models": models,
        "tasks": ", ".join("%s (level %s)" % (t.get("name"), t.get("level")) for t in (tasks or [])) or None,
        "repetitions": repetitions,
        "cool down": "%s s" % cool_down if cool_down is not None else None,
        "country": country,
        "baseline": _safe(lambda: harness.baseline.baseline_id) if harness is not None else None,
        "readers": [{"reader": r, "fidelity": f} for r, f in readers],
    }
    if con.json_output:
        con.result({"run_header": info})
        return
    con.line("")
    con.line(con.style("A-LEMS run", "bold"))
    for key in ("engine", "host", "invocation", "profile", "lock file", "config"):
        _kv(con, key, info[key], "config")
    for key in ("store", "database", "log dir", "error dir"):
        _kv(con, key, info[key], "store")
    for i, m in enumerate(models):
        _kv(con, "models" if i == 0 else "", m, "models")
    for key in ("tasks", "repetitions", "cool down", "country", "baseline"):
        _kv(con, key, info[key], "models")
    for i, (r, f) in enumerate(readers):
        _kv(con, "readers" if i == 0 else "", "%s  %s" % (r, con.style(f, "green" if f == "MEASURED" else "yellow")), "readers")


# Detail sections of a run card: (title, [(label, column, unit, scale)]).
_SECTIONS = [
    ("timing", [("task duration", "task_duration_ns", "s", 1e-9), ("framework overhead", "framework_overhead_ns", "s", 1e-9),
                ("pre task energy", "pre_task_energy_uj", "J", 1e-6), ("post task energy", "post_task_energy_uj", "J", 1e-6),
                ("avg task power", "avg_task_power_watts", "W", 1)]),
    ("energy domains", [("core", "core_energy_uj", "J", 1e-6), ("uncore", "uncore_energy_uj", "J", 1e-6),
                        ("dram", "dram_energy_uj", "J", 1e-6), ("idle baseline", "baseline_energy_uj", "J", 1e-6),
                        ("sample coverage", "energy_sample_coverage_pct", "%", 1), ("spbm coverage", "spbm_sample_coverage_pct", "%", 1)]),
    ("phases", [("planning", "planning_energy_uj", "J", 1e-6), ("execution", "execution_energy_uj", "J", 1e-6),
                ("synthesis", "synthesis_energy_uj", "J", 1e-6), ("inter phase", "inter_phase_energy_uj", "J", 1e-6),
                ("coverage", "phase_sample_coverage_pct", "%", 1)]),
    ("gpu", [("total", "gpu_total_energy_uj", "J", 1e-6), ("dynamic", "gpu_dynamic_energy_uj", "J", 1e-6),
             ("share of package", "gpu_pct_of_pkg", "%", 1), ("method", "gpu_attribution_method", "", None)]),
    ("cpu", [("ipc", "ipc", "", 1), ("cache miss rate", "cache_miss_rate", "", 1), ("frequency", "frequency_mhz", "MHz", 1),
             ("instructions", "instructions", "", 1), ("context switches", "total_context_switches", "", 1),
             ("c6 time", "c6_time_seconds", "s", 1), ("governor", "governor", "", None), ("turbo", "turbo_enabled", "", None)]),
    ("thermal", [("start", "start_temp_c", "C", 1), ("max", "max_temp_c", "C", 1), ("min", "min_temp_c", "C", 1),
                 ("delta", "thermal_delta_c", "C", 1), ("package", "package_temp_celsius", "C", 1),
                 ("throttle during run", "thermal_during_experiment", "", None), ("throttle at end", "thermal_now_active", "", None)]),
    ("system", [("background cpu", "background_cpu_percent", "%", 1), ("processes", "process_count", "", 1),
                ("rss memory", "rss_memory_mb", "MB", 1), ("interrupt rate", "interrupt_rate", "/s", 1),
                ("cold start", "is_cold_start", "", None)]),
    ("network", [("dns latency", "dns_latency_ms", "ms", 1), ("api latency", "api_latency_ms", "ms", 1),
                 ("bytes sent", "bytes_sent", "", 1), ("bytes received", "bytes_recv", "", 1),
                 ("tcp retransmits", "tcp_retransmits", "", 1), ("tpot", "tpot_ms", "ms", 1)]),
    ("orchestration", [("steps", "steps", "", 1), ("complexity", "complexity_score", "", 1),
                       ("planning time", "planning_time_ms", "ms", 1), ("execution time", "execution_time_ms", "ms", 1),
                       ("synthesis time", "synthesis_time_ms", "ms", 1)]),
    ("sustainability", [("carbon", "carbon_g", "g", 1), ("water", "water_ml", "ml", 1), ("methane", "methane_mg", "mg", 1),
                        ("energy per token", "energy_per_token", "J", 1)]),
]


def _fmt(value, unit, scale):
    """Format one value with its unit; text columns as stored."""
    if scale is None or not isinstance(value, (int, float)):
        return str(value)
    v = value * scale
    text = "{:,}".format(int(v)) if float(v).is_integer() and abs(v) >= 1000 else ("%.4g" % v)
    return (text + " " + unit).strip()


def _sections(row):
    """Present detail values per section from a runs row (NULL omitted)."""
    out, keys = {}, row.keys()
    for title, fields in _SECTIONS:
        vals = [(label, _fmt(row[col], unit, scale)) for label, col, unit, scale in fields
                if col in keys and row[col] is not None]
        if vals:
            out[title] = vals
    return out


def _run_linked_tables(conn):
    # type: (sqlite3.Connection) -> List[str]
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return [n for n in names if _safe(lambda n=n: any(
        c[1] == "run_id" for c in conn.execute("PRAGMA table_info('%s')" % n)), False)]


def _rows(conn, sql, *args):
    """Best effort query returning a list of rows."""
    return _safe(lambda: conn.execute(sql, args).fetchall(), []) or []


def _card(conn, run, tables):
    # type: (sqlite3.Connection, sqlite3.Row, List[str]) -> Dict[str, Any]
    """Collect one run card from the store."""
    rid = run["run_id"]
    uj = lambda v: None if v is None else round(v / 1e6, 4)  # noqa: E731
    exp = _rows(conn, "SELECT experiment_type, experiment_goal, provider, model_name, task_name, country_code, env_id FROM experiments WHERE exp_id=?", run["exp_id"])
    env = _rows(conn, "SELECT git_commit, git_dirty, python_version, kernel_version FROM environment_config WHERE env_id=?", exp[0]["env_id"]) if exp else []
    path = _rows(conn, "SELECT g.graph_id FROM stage_event e JOIN stage_graph g ON g.graph_hash=e.graph_hash WHERE e.run_id=? LIMIT 1", rid)
    stages = _rows(conn, "SELECT status, COUNT(*) FROM stage_event WHERE run_id=? GROUP BY status", rid)
    reasons = _rows(conn, "SELECT stage_id, reason FROM stage_event WHERE run_id=? AND reason IS NOT NULL AND reason != ''", rid)
    errors = _rows(conn, "SELECT COUNT(*) FROM stage_event WHERE run_id=? AND error_ref IS NOT NULL", rid)
    att = _rows(conn, "SELECT attempt_id, attempt_number, is_retry, is_winning, outcome, failure_type, normalized_score, pass_fail FROM goal_attempt WHERE run_id=?", rid)
    oq = _rows(conn, "SELECT normalized_score, pass_fail, judge_method, scorer_version FROM output_quality WHERE attempt_id=?", att[0]["attempt_id"]) if att else []
    rq = _rows(conn, "SELECT quality_score, rejection_reason FROM run_quality WHERE run_id=?", rid)
    llm = _rows(conn, "SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens), AVG(ttft_ms) FROM llm_interactions WHERE run_id=?", rid)
    spans = _rows(conn, "SELECT kind, COUNT(*), SUM(parent_span_id IS NULL), SUM(end_ns IS NULL) FROM spans WHERE run_id=? GROUP BY kind", rid)
    attr = _rows(conn, "SELECT attribution_method, attribution_model_version, isolation_level, idle_policy, attribution_coverage_pct, unattributed_energy_uj FROM energy_attribution WHERE run_id=?", rid)
    resid = _rows(conn, "SELECT status, COUNT(*) FROM attribution_residual WHERE run_id=? GROUP BY status", rid)
    inj = _rows(conn, "SELECT injected_type, target_tool, target_phase, status FROM failure_injection_log WHERE run_id=?", rid)
    tfail = _rows(conn, "SELECT COUNT(*) FROM tool_failure_events WHERE run_id=?", rid)
    rec = _rows(conn, "SELECT COUNT(*) FROM recovery_events WHERE run_id=?", rid)
    rows = {}
    for t in tables:
        n = _rows(conn, 'SELECT COUNT(*) FROM "%s" WHERE run_id=?' % t, rid)
        if n and n[0][0]:
            rows[t] = n[0][0]
    a = dict(att[0]) if att else {}
    store_dir = os.path.dirname(_state["store"] or "")
    return {
        "run_id": rid, "exp_id": run["exp_id"], "workflow": run["workflow_type"],
        "experiment": dict(exp[0]) if exp else None,
        "environment": dict(env[0]) if env else None,
        "path": path[0][0] if path else None,
        "attempt": a or None,
        "energy_j": {"pkg": uj(run["pkg_energy_uj"]), "dynamic": uj(run["dynamic_energy_uj"]),
                     "attributed": uj(run["attributed_energy_uj"])},
        "llm": {"calls": llm[0][0], "prompt_tokens": llm[0][1], "completion_tokens": llm[0][2],
                "ttft_ms": None if llm[0][3] is None else round(llm[0][3], 1)} if llm else None,
        "tools": run["tools_used"],
        "quality": {"score": oq[0]["normalized_score"] if oq else a.get("normalized_score"),
                    "pass_fail": oq[0]["pass_fail"] if oq else a.get("pass_fail"),
                    "judge": oq[0]["judge_method"] if oq else None,
                    "run_quality": rq[0]["quality_score"] if rq else None,
                    "issues": _issues(rq[0]["rejection_reason"]) if rq else None},
        "spans": {k: {"count": n, "roots": r, "open": o} for k, n, r, o in spans},
        "attribution": dict(attr[0]) if attr else None,
        "residual": {s: n for s, n in resid},
        "injection": [dict(i) for i in inj],
        "tool_failures": tfail[0][0] if tfail else 0,
        "recovery_events": rec[0][0] if rec else 0,
        "stages": {s: n for s, n in stages},
        "stage_reasons": {s: r for s, r in reasons},
        "error_records": errors[0][0] if errors else 0,
        "valid": run["experiment_valid"],
        "log_level": run["measurement_log_level"],
        "log_file": os.path.join(store_dir, "logs", "run_%s.jsonl" % run["global_run_id"]) if run["global_run_id"] else None,
        "rows_written": rows,
        "details": _sections(run),
    }


def _issues(raw):
    """Compact quality issues from the run_quality rejection JSON (hard, soft, missing)."""
    import json as _json
    try:
        d = _json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return raw
    parts = []
    for key, label in (("hard_failures", "hard"), ("soft_issues", "soft"), ("missing_telemetry", "missing")):
        if d.get(key):
            parts.append("%s %s" % (label, ",".join(d[key])))
    return "; ".join(parts) or None


def _join(d):
    """Render a dict as 'key value' pairs, skipping empty values."""
    return "  ".join("%s %s" % (k, v) for k, v in d.items() if v not in (None, "", {}))


def _render_card(con, c):
    # type: (Any, Dict[str, Any]) -> None
    a = c.get("attempt") or {}
    head = "run %s  %s  path %s" % (c["run_id"], c["workflow"], c.get("path") or "?")
    if a:
        head += "  attempt %s%s  outcome %s" % (a.get("attempt_number"), " (retry)" if a.get("is_retry") else "", a.get("outcome"))
    con.line("")
    con.line(con.style(head, "bold"))
    e = c.get("experiment") or {}
    _kv(con, "experiment", "exp %s  %s  %s" % (c["exp_id"], e.get("experiment_type") or "", e.get("experiment_goal") or ""), "config")
    env = c.get("environment") or {}
    if env:
        _kv(con, "environment", "git %s%s  python %s  kernel %s" % (
            env.get("git_commit"), " (dirty)" if env.get("git_dirty") else "", env.get("python_version"), env.get("kernel_version")), "config")
    en = c["energy_j"]
    _kv(con, "energy", "pkg %s J  dynamic %s J  attributed %s J" % (en["pkg"], en["dynamic"], en["attributed"]))
    if c.get("llm"):
        _kv(con, "llm", "%s calls  tokens in %s out %s  ttft %s ms" % tuple(c["llm"][k] for k in ("calls", "prompt_tokens", "completion_tokens", "ttft_ms")))
    _kv(con, "tools", c.get("tools"))
    q = {k: v for k, v in c["quality"].items() if v is not None}
    if q:
        verdict = q.get("pass_fail")
        style = "green" if verdict in (1, "pass", True) else "red" if verdict in (0, "fail", False) else "green"
        _kv(con, "quality", con.style(_join(q), style))
    if c["spans"]:
        _kv(con, "spans", "  ".join("%s %d (roots %s, open %s)" % (k, v["count"], v["roots"], v["open"]) for k, v in sorted(c["spans"].items())))
    if c.get("attribution"):
        _kv(con, "attribution", _join(c["attribution"]))
    if c["residual"]:
        _kv(con, "residual", _join(c["residual"]), "attribution")
    for i in c["injection"]:
        _kv(con, "injection", con.style(_join(i), "red"))
    if c["tool_failures"] or c["recovery_events"]:
        _kv(con, "failures", "tool failures %s  recovery events %s" % (c["tool_failures"], c["recovery_events"]))
    for title, vals in c.get("details", {}).items():
        _kv(con, title, "  ".join("%s %s" % (k, v) for k, v in vals))
    _kv(con, "stages", "  ".join("%s %s" % (n, s) for s, n in sorted(c["stages"].items())) or "none recorded")
    for s, r in c["stage_reasons"].items():
        _kv(con, "", "%s: %s" % (s, r), "stages")
    _kv(con, "valid", con.style("yes" if c["valid"] else "no", "green" if c["valid"] else "red"))
    _kv(con, "errors", con.style(str(c["error_records"]), "red" if c["error_records"] else "green"))
    _kv(con, "log file", c.get("log_file"), "files")
    _kv(con, "rows written", "  ".join("%s %d" % (t, n) for t, n in sorted(c["rows_written"].items())) or "none")


def footer():
    # type: () -> None
    """Render one card per run of the experiments created since header()."""
    con = get_console()
    store = _state["store"] or _safe(_store_path)
    if not store:
        return
    conn = _safe(lambda: _ro(store))
    if conn is None:
        return
    floor = _state["exp_floor"] or 0
    runs = _rows(conn, "SELECT * FROM runs WHERE exp_id > ? ORDER BY run_id", floor)
    tables = _safe(lambda: _run_linked_tables(conn), []) or []
    cards = [c for c in (_safe(lambda r=r: _card(conn, r, tables)) for r in runs) if c]
    conn.close()
    if con.json_output:
        con.result({"run_cards": cards, "store": store})
        return
    con.line("")
    con.line(con.style("run report", "bold") + "  " + store)
    for c in cards:
        _render_card(con, c)
