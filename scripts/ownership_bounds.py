#!/usr/bin/env python3
"""
Ownership bounds: how many joules each agent phase can legitimately claim.

A cumulative energy counter reports increments over sampling intervals. When an
interval straddles two phases, the increment cannot be assigned to either phase
without an allocation assumption. For a phase P of a run (union of all its
windows W) and a domain d:

    lower  L = sum of increments whose support lies wholly inside W
    upper  U = sum of increments whose support overlaps W
    prop     = sum of increments x (overlap / support length)   (the usual estimate)

DCGM (domain value "dcgm" in config) is read from gpu_samples with its own
sample start and end. Tool calls (event_type tool_call) are reported as an extra
window group "tool_call" next to the phases.

Support of an increment = [t_end - interval - widen, t_end], where widen covers
the counter's own update period (an increment seen in one poll may have accrued
up to one counter period earlier). A claimed phase saving holds under every
admissible allocation only if it exceeds the bound widths.

Read only. Output CSV only:
  ownership_bounds_runs.csv     one row per run, phase, domain
  ownership_bounds_summary.csv  per phase and domain over all runs (sums, so
                                 ratios are ratio of sums)
Missing values are empty cells, never 0.

Usage:
  python scripts/ownership_bounds.py --db <store> --out-dir <dir> [--runs 24-35]
"""
import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("ownership_bounds")

Interval = Tuple[int, int]
RUN_FIELDS = ["run_id", "workflow_type", "phase", "domain", "n_windows", "phase_s",
              "lower_j", "prop_j", "upper_j", "width_j", "width_share",
              "n_inside", "n_straddling", "sample_cover", "note"]
SUM_FIELDS = ["phase", "domain", "n_runs", "phase_s", "lower_j", "prop_j", "upper_j",
              "width_j", "width_share"]


def load_config(path):
    # type: (Path) -> Dict
    """Settings section ownership_bounds from app_settings.yaml."""
    with open(path) as fh:
        cfg = (yaml.safe_load(fh) or {}).get("ownership_bounds")
    if not cfg:
        raise SystemExit("ownership_bounds section missing in %s" % path)
    return cfg


def open_ro(db):
    # type: (str) -> sqlite3.Connection
    """Read only connection (analysis script; never writes the store)."""
    return sqlite3.connect("file:%s?mode=ro" % db, uri=True)


def merge(windows):
    # type: (List[Interval]) -> List[Interval]
    """Union of windows as sorted, non overlapping intervals."""
    out = []  # type: List[Interval]
    for s, e in sorted(w for w in windows if w[1] > w[0]):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
            continue
        out.append((s, e))
    return out


def overlap(a, union):
    # type: (Interval, List[Interval]) -> int
    """Overlap in ns of interval a with a union of intervals (small unions only)."""
    return sum(max(0, min(a[1], e) - max(a[0], s)) for s, e in union)


def bounds(incs, union):
    # type: (List[Tuple[Interval, float]], List[Interval]) -> Dict
    """
    Lower, proportional and upper energy of a phase union from increments.
    Increments are visited in order of support start and the union is sorted and
    disjoint, so a window that ends before one support starts ends before every
    later support starts: the pointer only moves forward and cost is close to
    linear in increments plus windows.
    """
    lo = pr = up = 0.0
    n_in = n_str = 0
    j = 0
    for sup, e in sorted(incs, key=lambda x: x[0][0]):
        while j < len(union) and union[j][1] <= sup[0]:
            j += 1  # windows ending before this support starts can never overlap later supports
        ov, k = 0, j
        while k < len(union) and union[k][0] < sup[1]:
            ov += max(0, min(sup[1], union[k][1]) - max(sup[0], union[k][0]))
            k += 1
        if ov <= 0:
            continue
        length = sup[1] - sup[0]
        inside = ov >= length
        lo, up, pr = lo + (e if inside else 0.0), up + e, pr + e * ov / float(length)
        n_in, n_str = n_in + int(inside), n_str + int(not inside)
    return {"lower": lo, "prop": pr, "upper": up, "n_inside": n_in, "n_straddling": n_str}


def increments_bulk(conn, lo, hi, domain_id, widen_ns):
    # type: (sqlite3.Connection, int, int, int, int) -> Dict[int, List[Tuple[Interval, float]]]
    """(support, joules) per run for a run id range and one SPBM domain; one query per chunk."""
    out = {}  # type: Dict[int, List[Tuple[Interval, float]]]
    for rid, t, iv, e in conn.execute(
            "SELECT s.run_id, s.timestamp_ns, s.interval_ns, d.energy_uj FROM energy_samples_v2 s "
            "JOIN energy_sample_domains d ON d.sample_id = s.sample_id "
            "WHERE s.run_id BETWEEN ? AND ? AND d.domain_id = ? AND d.energy_uj IS NOT NULL "
            "AND s.interval_ns > 0", (lo, hi, domain_id)):
        out.setdefault(rid, []).append(((t - iv - widen_ns, t), e / 1e6))
    return out


def dcgm_bulk(conn, lo, hi, widen_ns):
    # type: (sqlite3.Connection, int, int, int) -> Dict[int, List[Tuple[Interval, float]]]
    """(support, joules) per run from DCGM gpu_samples (own start and end) for a run id range."""
    out = {}  # type: Dict[int, List[Tuple[Interval, float]]]
    for rid, a, b, u in conn.execute(
            "SELECT run_id, sample_start_ns, sample_end_ns, energy_uj FROM gpu_samples "
            "WHERE run_id BETWEEN ? AND ? AND energy_uj IS NOT NULL AND sample_end_ns > sample_start_ns",
            (lo, hi)):
        out.setdefault(rid, []).append(((a - widen_ns, b), u / 1e6))
    return out


def increments(conn, run_id, domain_id, widen_ns):
    # type: (sqlite3.Connection, int, int, int) -> List[Tuple[Interval, float]]
    """Single run convenience wrapper (tests, small stores)."""
    return increments_bulk(conn, run_id, run_id, domain_id, widen_ns).get(run_id, [])


def dcgm_increments(conn, run_id, widen_ns):
    # type: (sqlite3.Connection, int, int) -> List[Tuple[Interval, float]]
    """Single run convenience wrapper for DCGM."""
    return dcgm_bulk(conn, run_id, run_id, widen_ns).get(run_id, [])


def phase_windows_bulk(conn, lo, hi, groups):
    # type: (sqlite3.Connection, int, int, List[str]) -> Dict[int, Dict[str, List[Interval]]]
    """
    Windows per run and group for a run id range. Groups: "phases" (planning,
    execution, waiting, synthesis) and "tool_call" (event_type tool_call, timed
    directly around each tool dispatch). Old stores use only tool_call: their
    phase boundary timing predates the phase boundary fixes.
    """
    out = {}  # type: Dict[int, Dict[str, List[Interval]]]
    for rid, ph, ev, s, e in conn.execute(
            "SELECT run_id, phase, event_type, start_time_ns, end_time_ns FROM orchestration_events "
            "WHERE run_id BETWEEN ? AND ? AND phase IS NOT NULL AND end_time_ns > start_time_ns",
            (lo, hi)):
        w = out.setdefault(rid, {})
        if "phases" in groups:
            w.setdefault(ph, []).append((int(s), int(e)))
        if ev == "tool_call" and "tool_call" in groups:
            w.setdefault("tool_call", []).append((int(s), int(e)))
    return out


def phase_windows(conn, run_id):
    # type: (sqlite3.Connection, int) -> Dict[str, List[Interval]]
    """Single run convenience wrapper with both groups."""
    return phase_windows_bulk(conn, run_id, run_id, ["phases", "tool_call"]).get(run_id, {})


def chunk_rows(conn, runs, cfg, groups):
    # type: (sqlite3.Connection, List[Tuple[int, str]], Dict, List[str]) -> List[Dict]
    """Rows for a chunk of runs: one bulk query per meter, then bounds per run."""
    lo, hi = runs[0][0], runs[-1][0]
    widen = int(cfg["support_widen_ms"] * 1e6)
    wins = phase_windows_bulk(conn, lo, hi, groups)
    rows = []
    for dname, did in cfg["domains"].items():
        incs_by_run = (dcgm_bulk(conn, lo, hi, widen) if str(did) == "dcgm"
                       else increments_bulk(conn, lo, hi, int(did), widen))
        for run_id, wf in runs:
            rows.extend(meter_rows(run_id, wf, dname, wins.get(run_id, {}),
                                   incs_by_run.get(run_id, []), cfg))
    return rows


def meter_rows(run_id, wf, dname, phases, incs, cfg):
    # type: (int, str, str, Dict[str, List[Interval]], list, Dict) -> List[Dict]
    """Rows of one run and meter for every window group."""
    span = (min(i[0][0] for i in incs), max(i[0][1] for i in incs)) if incs else None
    return [phase_row(run_id, wf, ph, dname, merge(w), incs, span, cfg)
            for ph, w in sorted(phases.items())]


def phase_row(run_id, wf, phase, dname, union, incs, span, cfg):
    # type: (int, str, str, str, List[Interval], list, Optional[Interval], Dict) -> Dict
    """One output row; no samples or poor sample cover is noted, values left empty."""
    total = sum(e - s for s, e in union)
    row = {"run_id": run_id, "workflow_type": wf, "phase": phase, "domain": dname,
           "n_windows": len(union), "phase_s": round(total / 1e9, 4)}
    if not incs or total <= 0:
        return dict(row, note="no samples")
    cover = overlap(span, union) / float(total)
    row["sample_cover"] = round(cover, 4)
    if cover < cfg["min_sample_cover"]:
        return dict(row, note="phase outside sample span (clock basis?)")
    b = bounds(incs, union)
    width = b["upper"] - b["lower"]
    return dict(row, lower_j=round(b["lower"], 4), prop_j=round(b["prop"], 4),
                upper_j=round(b["upper"], 4), width_j=round(width, 4),
                width_share=round(width / b["upper"], 4) if b["upper"] > 0 else None,
                n_inside=b["n_inside"], n_straddling=b["n_straddling"])


def summarize(rows):
    # type: (List[Dict]) -> List[Dict]
    """Ratio of sums per phase and domain over runs with values."""
    acc = {}  # type: Dict[Tuple[str, str], Dict]
    for r in rows:
        if r.get("upper_j") is None:
            continue
        a = acc.setdefault((r["phase"], r["domain"]),
                           {"n_runs": 0, "phase_s": 0.0, "lower_j": 0.0, "prop_j": 0.0, "upper_j": 0.0})
        for k in ("phase_s", "lower_j", "prop_j", "upper_j"):
            a[k] += r[k]
        a["n_runs"] += 1
    out = []
    for (ph, dom), a in sorted(acc.items()):
        w = a["upper_j"] - a["lower_j"]
        out.append(dict(a, phase=ph, domain=dom, width_j=round(w, 4),
                        width_share=round(w / a["upper_j"], 4) if a["upper_j"] > 0 else None))
    return out


def parse_runs(spec):
    # type: (Optional[str]) -> Optional[Tuple[int, int]]
    """'24-35' to (24, 35); None means all runs with phases."""
    if not spec:
        return None
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi or lo)


def select_runs(conn, rng):
    # type: (sqlite3.Connection, Optional[Tuple[int, int]]) -> List[Tuple[int, str]]
    """Runs that have orchestration events, optionally limited to a run id range."""
    q = ("SELECT DISTINCT r.run_id, r.workflow_type FROM runs r JOIN orchestration_events o "
         "ON o.run_id = r.run_id")
    args = ()  # type: tuple
    if rng:
        q, args = q + " WHERE r.run_id BETWEEN ? AND ?", rng
    return conn.execute(q + " ORDER BY r.run_id", args).fetchall()


def write_csv(path, fields, rows):
    # type: (Path, List[str], List[Dict]) -> None
    """CSV with a fixed header; None is written as an empty cell."""
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    """Compute bounds for the selected runs and write both CSVs."""
    ap = argparse.ArgumentParser(description="Phase ownership bounds (read only, CSV).")
    ap.add_argument("--db", required=True, help="store path (opened read only)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--runs", help="run id range, e.g. 24-35")
    ap.add_argument("--config", default=str(ROOT / "config" / "app_settings.yaml"))
    ap.add_argument("--tool-calls-only", action="store_true",
                    help="old stores: tool call windows only (phase boundary timing predates the fixes)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(Path(args.config))
    conn = open_ro(args.db)
    runs = select_runs(conn, parse_runs(args.runs))
    groups = ["tool_call"] if args.tool_calls_only else ["phases", "tool_call"]
    size = int(cfg.get("chunk_runs", 200))
    rows = []  # type: List[Dict]
    for i in range(0, len(runs), size):
        rows.extend(chunk_rows(conn, runs[i:i + size], cfg, groups))
        logger.info("runs %d of %d", min(i + size, len(runs)), len(runs))
    out = Path(args.out_dir) / ("ownership_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=False)
    write_csv(out / "ownership_bounds_runs.csv", RUN_FIELDS, rows)
    summ = summarize(rows)
    write_csv(out / "ownership_bounds_summary.csv", SUM_FIELDS, summ)
    print("runs %d, rows %d, CSV in %s" % (len(runs), len(rows), out))
    for s in summ:
        print("  %-10s %-8s lower %8.2f J  prop %8.2f J  upper %8.2f J  width share %s"
              % (s["phase"], s["domain"], s["lower_j"], s["prop_j"], s["upper_j"], s["width_share"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
