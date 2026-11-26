#!/usr/bin/env python3
"""
Capability registry and store audit (chunk 45, 45.5b and 45.5c).

For every experiment in every configured store: which measurement capabilities
its runs carry, which analyses it is usable for, and audit flags (early DCGM
row cap, DCGM coverage of the run window, runs without energy samples).

Cells are three state: yes, no, unknown. "unknown" means the store cannot tell
(a table without run_id, or phase_boundary_min_schema not set); it is never
reported as yes or no. Shares are empty cells when there are no runs.

Read only. Output CSV only:
  capabilities_experiments.csv  one row per store and experiment
  capabilities_stores.csv       one row per store
  capabilities_runs.csv         one row per run (with --per-run)

Usage:
  python scripts/experiment_capabilities.py --out-dir <dir> [--stores raf_bfcl,gb10_prod] [--per-run]
"""
import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("experiment_capabilities")
YES, NO, UNKNOWN = "yes", "no", "unknown"


def load_config(path):
    # type: (Path) -> Dict
    """config/experiment_capabilities.yaml; every section is required."""
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    for key in ("stores", "min_run_share", "audit", "capabilities", "usable_for"):
        if key not in cfg:
            raise SystemExit("%s missing in %s" % (key, path))
    for exp, caps in cfg["usable_for"].items():
        bad = [c for c in caps if c not in cfg["capabilities"]]
        if bad:
            raise SystemExit("usable_for %s names unknown capabilities %s" % (exp, bad))
    return cfg


def open_ro(db):
    # type: (str) -> sqlite3.Connection
    """Read only connection; the registry never writes a store."""
    return sqlite3.connect("file:%s?mode=ro" % db, uri=True)


def columns(conn, table):
    # type: (sqlite3.Connection, str) -> Optional[Set[str]]
    """Column names of a table, None when the table does not exist."""
    cols = {r[1] for r in conn.execute("SELECT * FROM pragma_table_info(?)", (table,))}
    return cols or None


def schema_version(conn):
    # type: (sqlite3.Connection) -> Optional[int]
    """Highest applied schema version, None when the store has no schema_version table."""
    if not columns(conn, "schema_version"):
        return None
    return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]


def domain_sets(conn):
    # type: (sqlite3.Connection) -> Dict[int, Set[int]]
    """Domains with at least one non null increment, per run (one scan of the store)."""
    out = {}  # type: Dict[int, Set[int]]
    for rid, dom in conn.execute(
            "SELECT s.run_id, d.domain_id FROM energy_samples_v2 s "
            "JOIN energy_sample_domains d ON d.sample_id = s.sample_id "
            "WHERE d.energy_uj IS NOT NULL GROUP BY s.run_id, d.domain_id"):
        out.setdefault(rid, set()).add(dom)
    return out


def probe(conn, spec, ctx):
    # type: (sqlite3.Connection, Dict, Dict) -> Dict[int, str]
    """Per run state of one capability; runs absent from the result have state no."""
    kind, runs = spec["probe"], ctx["runs"]
    if kind == "domain":
        need = set(spec["domain_ids"])
        return {r: YES for r, doms in ctx["domains"].items() if need <= doms}
    if kind == "schema_min":
        state = UNKNOWN if ctx["min_schema"] is None or ctx["schema"] is None else (
            YES if ctx["schema"] >= ctx["min_schema"] else NO)
        return {r: state for r in runs}
    if kind == "run_column":
        if spec["column"] not in ctx["run_cols"]:
            return {}
        return {r: YES for (r,) in conn.execute(
            "SELECT run_id FROM runs WHERE %s IS NOT NULL" % spec["column"])}
    if kind == "table_rows":
        cols = columns(conn, spec["table"])
        if cols is None:
            return {}
        if "run_id" not in cols:
            return {r: UNKNOWN for r in runs}
        where = (" WHERE " + spec["where"]) if spec.get("where") else ""
        return {r: YES for (r,) in conn.execute(
            "SELECT DISTINCT run_id FROM %s%s" % (spec["table"], where)) if r is not None}
    raise SystemExit("unknown probe kind %s" % kind)


def dcgm_audit(conn, runs, audit):
    # type: (sqlite3.Connection, Dict[int, Dict], Dict) -> Dict[int, Dict]
    """Per run DCGM row count, capped flag and coverage of the run window."""
    out = {}  # type: Dict[int, Dict]
    if columns(conn, "gpu_samples") is None:
        return out
    for rid, n, a, b in conn.execute("SELECT run_id, COUNT(*), MIN(sample_start_ns), MAX(sample_end_ns) "
                                     "FROM gpu_samples GROUP BY run_id"):
        run = runs.get(rid)
        if run is None:
            continue
        lo, hi = run["t_from"], run["t_to"]
        cover = None
        if lo is not None and hi is not None and hi > lo and a is not None and b is not None:
            cover = max(0, min(b, hi) - max(a, lo)) / float(hi - lo)
        out[rid] = {"dcgm_rows": n, "dcgm_capped": int(n == int(audit["dcgm_cap_rows"])),
                    "dcgm_cover": None if cover is None else round(cover, 4)}
    return out


def load_runs(conn, audit):
    # type: (sqlite3.Connection, Dict) -> Dict[int, Dict]
    """run_id to exp_id and the run window named in the audit config."""
    q = "SELECT run_id, exp_id, %s, %s FROM runs" % (audit["run_window_from"], audit["run_window_to"])
    return {r: {"exp_id": e, "t_from": a, "t_to": b} for r, e, a, b in conn.execute(q)}


def load_experiments(conn):
    # type: (sqlite3.Connection) -> Dict[int, Dict]
    """Descriptive experiment fields that exist in this store."""
    cols = columns(conn, "experiments") or set()
    want = [c for c in ("name", "model_name", "provider", "workflow_type", "task_name",
                        "experiment_type", "hw_id") if c in cols]
    out = {}
    for row in conn.execute("SELECT exp_id%s FROM experiments" % "".join(", " + c for c in want)):
        out[row[0]] = dict(zip(want, row[1:]))
    return out


def cell(states, min_share):
    # type: (List[str], float) -> (str, Optional[float])
    """Experiment level state and share of runs with yes."""
    if not states:
        return UNKNOWN, None
    if all(s == UNKNOWN for s in states):
        return UNKNOWN, None
    share = sum(1 for s in states if s == YES) / float(len(states))
    return (YES if share >= min_share else NO), round(share, 4)


def usable(states, required):
    # type: (Dict[str, str], List[str]) -> (str, str)
    """yes when every required capability is yes; unknown when none is no; blocking names."""
    no = [c for c in required if states[c] == NO]
    unk = [c for c in required if states[c] == UNKNOWN]
    if no:
        return NO, " ".join(no)
    if unk:
        return UNKNOWN, " ".join(unk)
    return YES, ""


def store_rows(name, path, cfg):
    # type: (str, str, Dict) -> (List[Dict], Dict, List[Dict])
    """Experiment rows, the store row and run rows for one store."""
    conn = open_ro(path)
    audit = cfg["audit"]
    runs = load_runs(conn, audit)
    ctx = {"runs": runs, "domains": domain_sets(conn), "schema": schema_version(conn),
           "min_schema": cfg.get("phase_boundary_min_schema"), "run_cols": columns(conn, "runs") or set()}
    caps = {c: probe(conn, spec, ctx) for c, spec in cfg["capabilities"].items()}
    dcgm = dcgm_audit(conn, runs, audit)
    exps = load_experiments(conn)
    run_rows, by_exp = [], {}  # type: List[Dict], Dict[int, List[Dict]]
    for rid, run in runs.items():
        row = {"store": name, "run_id": rid, "exp_id": run["exp_id"],
               "energy_samples": YES if rid in ctx["domains"] else NO}
        row.update({c: caps[c].get(rid, NO) for c in caps})
        row.update(dcgm.get(rid, {"dcgm_rows": None, "dcgm_capped": None, "dcgm_cover": None}))
        run_rows.append(row)
        by_exp.setdefault(run["exp_id"], []).append(row)
    exp_rows = [experiment_row(name, eid, rows, exps.get(eid, {}), cfg) for eid, rows in sorted(by_exp.items())]
    store = {"store": name, "path": path, "schema_version": ctx["schema"], "n_experiments": len(by_exp),
             "n_runs": len(runs), "n_runs_without_samples": sum(1 for r in run_rows if r["energy_samples"] == NO),
             "n_dcgm_capped": sum(1 for r in run_rows if r["dcgm_capped"] == 1)}
    store.update({"runs_" + c: sum(1 for r in run_rows if r[c] == YES) for c in caps})
    conn.close()
    return exp_rows, store, run_rows


def experiment_row(store, exp_id, rows, meta, cfg):
    # type: (str, int, List[Dict], Dict, Dict) -> Dict
    """One registry row: capability cells, usable_for cells, audit counts."""
    out = {"store": store, "exp_id": exp_id, "n_runs": len(rows)}
    out.update(meta)
    states = {}
    for c in cfg["capabilities"]:
        states[c], out["share_" + c] = cell([r[c] for r in rows], float(cfg["min_run_share"]))
        out[c] = states[c]
    for exp, required in cfg["usable_for"].items():
        out["usable_for_" + exp], out["blocking_" + exp] = usable(states, required)
    covers = sorted(r["dcgm_cover"] for r in rows if r["dcgm_cover"] is not None)
    out["n_runs_without_samples"] = sum(1 for r in rows if r["energy_samples"] == NO)
    out["n_dcgm_capped"] = sum(1 for r in rows if r["dcgm_capped"] == 1)
    out["dcgm_cover_median"] = covers[len(covers) // 2] if covers else None
    out["n_dcgm_cover_low"] = (sum(1 for c in covers if c < float(cfg["audit"]["dcgm_min_cover"]))
                               if covers else None)
    return out


def write_csv(path, rows):
    # type: (Path, List[Dict]) -> None
    """CSV with the union of keys in first seen order; None is an empty cell."""
    fields = []  # type: List[str]
    for r in rows:
        fields.extend(k for k in r if k not in fields)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    """Registry over the selected stores; prints one line per store."""
    ap = argparse.ArgumentParser(description="Capability registry and store audit (read only, CSV).")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stores", help="comma separated store names from the config (default all)")
    ap.add_argument("--per-run", action="store_true", help="also write capabilities_runs.csv")
    ap.add_argument("--config", default=str(ROOT / "config" / "experiment_capabilities.yaml"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(Path(args.config))
    names = args.stores.split(",") if args.stores else list(cfg["stores"])
    exp_rows, store_list, run_rows = [], [], []  # type: List[Dict], List[Dict], List[Dict]
    for name in names:
        path = cfg["stores"].get(name)
        if path is None:
            raise SystemExit("store %s not in config" % name)
        if not Path(path).is_file():
            logger.warning("store %s missing: %s", name, path)
            continue
        logger.info("store %s", name)
        e, s, r = store_rows(name, path, cfg)
        exp_rows.extend(e)
        store_list.append(s)
        run_rows.extend(r)
    out = Path(args.out_dir) / ("capabilities_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=False)
    write_csv(out / "capabilities_experiments.csv", exp_rows)
    write_csv(out / "capabilities_stores.csv", store_list)
    if args.per_run:
        write_csv(out / "capabilities_runs.csv", run_rows)
    for s in store_list:
        print("%-18s schema %-5s exps %5d runs %6d no samples %5d dcgm capped %4d"
              % (s["store"], s["schema_version"], s["n_experiments"], s["n_runs"],
                 s["n_runs_without_samples"], s["n_dcgm_capped"]))
    for exp in cfg["usable_for"]:
        n = sum(1 for r in exp_rows if r["usable_for_" + exp] == YES)
        print("usable_for_%-30s %d experiments" % (exp, n))
    print("CSV in %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
