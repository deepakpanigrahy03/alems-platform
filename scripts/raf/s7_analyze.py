#!/usr/bin/env python3
"""
RAF LH 01 S7 analysis (design v2 FINAL sections 7.2, 8, 9). Read only.

Inputs: the sandbox store (opened read only) and pilot_ledger.jsonl.
Outputs (next to the ledger): s7_goals.csv (one row per valid goal execution),
s7_cells.csv (per cell and policy), and s7_report.md (decision per cell).

Energy bases, per goal = attempt 1 + attempt 2:
    E_attr      goal_attempt.energy_uj (stored, process attributed)
    E_node_dyn  runs.pkg_energy_uj - P_idle_pkg * duration (PACKAGE covers the
                GB10 SoC including the GPU, so the GPU is not added again)
Tokens per goal:
    billed      sum of prompt and completion tokens over both attempts
    uncached    billed minus request level cached tokens (raf.llm_usage)
Decision (design 9): per cell, energy winner vs token winner; disagreement if
the paired bootstrap 95 percent CI of EpG(token winner) - EpG(energy winner)
excludes zero. Bootstrap resamples tasks within the cell (pairs kept).

Usage:  python3 s7_analyze.py [--idle-w 38.16255] [--boot 2000] [--partial]
"""

import argparse
import collections
import csv
import json
import os
import random
import sqlite3

STORE = "/mnt/alems-data/gn100-2b96/users/dpani/sandboxes/raf-mlsys-2027/experiments.db"
LEDGER = os.path.join(os.path.dirname(STORE), "artifacts", "raf_pilot", "pilot_ledger.jsonl")
POLICIES = ["FR_cold", "FR_warm", "SR_cold", "SR_warm"]


def _args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-w", type=float, default=38.16255, help="idle PACKAGE watts")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--partial", action="store_true", help="label report as preliminary")
    return ap.parse_args()


def load_ledger():
    """Last record per goal_id; only valid goal executions kept."""
    recs = {}
    for line in open(LEDGER):
        r = json.loads(line)
        recs[r["goal_id"]] = r
    return {g: r for g, r in recs.items() if not r["validity"]}


def goal_rows(conn, ledger, idle_w):
    """One dict per valid goal with energy, tokens, success."""
    out = []
    for gid, r in ledger.items():
        att = conn.execute(
            "SELECT ga.attempt_number, ga.energy_uj, r.pkg_energy_uj, r.duration_ns, "
            "ga.run_id, ga.span_id FROM goal_attempt ga JOIN runs r ON r.run_id=ga.run_id "
            "WHERE ga.goal_id=? ORDER BY ga.attempt_number", (gid,)).fetchall()
        succ = conn.execute("SELECT success FROM goal_execution WHERE goal_id=?",
                            (gid,)).fetchone()
        if not att or succ is None:
            continue
        e_attr = sum((a[1] or 0) for a in att) / 1e6
        e_node = sum((a[2] or 0) / 1e6 - idle_w * (a[3] or 0) / 1e9 for a in att)
        billed, cached = 0, 0
        for a in att:
            t = conn.execute("SELECT COALESCE(SUM(prompt_tokens),0)+COALESCE(SUM(completion_tokens),0) "
                             "FROM llm_interactions WHERE run_id=?", (a[4],)).fetchone()[0]
            billed += t or 0
            cached += _cached(conn, a[5])
        p = r["plan"]
        out.append({"goal_id": gid, "task": p["task"], "cls": p["cls"], "point": p["point"],
                    "policy": p["policy"], "success": int(succ[0] or 0),
                    "e_attr_j": e_attr, "e_node_dyn_j": e_node,
                    "tok_billed": billed, "tok_uncached": billed - cached,
                    "calls": sum(_calls(conn, a[4]) for a in att)})
    return out


def _cached(conn, span_id):
    """Cached tokens of one attempt from the raf.llm_usage annotation."""
    row = conn.execute("SELECT payload FROM span_annotations WHERE span_id=? AND "
                       "annotation_type='raf.llm_usage' ORDER BY rowid DESC LIMIT 1",
                       (span_id,)).fetchone()
    if not row:
        return 0
    calls = json.loads(row[0]).get("calls", [])
    return sum(c.get("cached_tokens") or 0 for c in calls)


def _calls(conn, run_id):
    return conn.execute("SELECT COUNT(*) FROM llm_interactions WHERE run_id=?",
                        (run_id,)).fetchone()[0]


def per_goal(rows, metric):
    """Metric per successful goal for a list of goal rows (None if no success)."""
    s = sum(r["success"] for r in rows)
    return sum(r[metric] for r in rows) / s if s else None


def cell_table(rows):
    """{(cls, point): {policy: [rows]}} for injected goals."""
    t = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r["policy"] != "B0":
            t[(r["cls"], r["point"])][r["policy"]].append(r)
    return t


def argmin(pols, metric):
    vals = {p: per_goal(rs, metric) for p, rs in pols.items()}
    vals = {p: v for p, v in vals.items() if v is not None}
    return (min(vals, key=vals.get), vals) if vals else (None, vals)


def boot_diff(pols, metric, a, b, n, rng):
    """95 percent CI of metric(a) - metric(b), resampling tasks (pairs kept)."""
    tasks = sorted({r["task"] for rs in pols.values() for r in rs})
    by = {p: collections.defaultdict(list) for p in (a, b)}
    for p in (a, b):
        for r in pols[p]:
            by[p][r["task"]].append(r)
    diffs = []
    for _ in range(n):
        pick = [rng.choice(tasks) for _ in tasks]
        ra = [r for t in pick for r in by[a][t]]
        rb = [r for t in pick for r in by[b][t]]
        va, vb = per_goal(ra, metric), per_goal(rb, metric)
        if va is not None and vb is not None:
            diffs.append(va - vb)
    if len(diffs) < n * 0.5:
        return None
    diffs.sort()
    return diffs[int(0.025 * len(diffs))], diffs[int(0.975 * len(diffs)) - 1]


def decide(table, energy, tokens, n, rng):
    """Per cell decision lines for one energy basis and one token metric."""
    lines, outcome = [], []
    for cell in sorted(table):
        pols = table[cell]
        pe, ve = argmin(pols, energy)
        pt, vt = argmin(pols, tokens)
        ntask = len({r["task"] for rs in pols.values() for r in rs})
        if pe is None or pt is None:
            lines.append((cell, ntask, pe, pt, None, "no success"))
            continue
        if pe == pt:
            lines.append((cell, ntask, pe, pt, None, "agree"))
            outcome.append("agree")
            continue
        ci = boot_diff(pols, energy, pt, pe, n, rng)
        tag = "DISAGREE" if ci and ci[0] > 0 else "point only"
        lines.append((cell, ntask, pe, pt, ci, tag))
        outcome.append(tag)
    return lines, outcome


def pooled_early(table, rng, n):
    """Secondary test: early cells pooled; token winner (uncached) vs SR_warm, per task pairs."""
    units, cells = collections.defaultdict(list), []
    for cell in sorted(table):
        if cell[1] != "early":
            continue
        pols = table[cell]
        pt, _ = argmin(pols, "tok_uncached")
        if pt is None or "SR_warm" not in pols:
            continue
        tok = {r["task"]: r for r in pols[pt]}
        ref = {r["task"]: r for r in pols["SR_warm"]}
        diffs = []
        for t, r in tok.items():
            if t in ref and r["success"] and ref[t]["success"]:
                d = r["e_node_dyn_j"] - ref[t]["e_node_dyn_j"]
                units[t].append(d)
                diffs.append(d)
        if diffs:
            cells.append((cell[0], pt, sum(diffs) / len(diffs)))
    tasks = sorted(units)
    head = ["", "## Pooled early cells (secondary: E_node_dyn, token winner by uncached tokens minus SR_warm)", ""]
    if not tasks:
        return head + ["no data"]
    allv = [d for t in tasks for d in units[t]]
    boots = []
    for _ in range(n):
        pick = [rng.choice(tasks) for _ in tasks]
        v = [d for t in pick for d in units[t]]
        boots.append(sum(v) / len(v))
    boots.sort()
    lo, hi = boots[int(0.025 * n)], boots[int(0.975 * n) - 1]
    out = head + ["pairs %d over %d tasks; mean difference %.1f J; 95 percent CI %.1f to %.1f J" % (
                      len(allv), len(tasks), sum(allv) / len(allv), lo, hi),
                  "cells where the token winner costs more than SR_warm: %d of %d" % (
                      sum(1 for c in cells if c[2] > 0), len(cells)), "",
                  "| cell | token winner | mean difference J |", "|---|---|---|"]
    out += ["| %s early | %s | %.1f |" % c for c in cells]
    out += ["", "SR_warm was chosen as the comparator from the same data; this test is secondary and does not replace the per cell rule."]
    return out


def main():
    a = _args()
    rng = random.Random(a.seed)
    conn = sqlite3.connect("file:%s?mode=ro" % STORE, uri=True)
    ledger = load_ledger()
    rows = goal_rows(conn, ledger, a.idle_w)
    out_dir = os.path.dirname(LEDGER)
    with open(os.path.join(out_dir, "s7_goals.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    table = cell_table(rows)
    rep = ["# S7 %s report" % ("PRELIMINARY" if a.partial else "pilot"),
           "", "valid goals: %d; idle PACKAGE W: %.3f" % (len(rows), a.idle_w), ""]
    b0 = [r for r in rows if r["policy"] == "B0"]
    if b0:
        rep.append("B0 EpG node_dyn %.1f J, attr %.1f J, success %d/%d" % (
            per_goal(b0, "e_node_dyn_j") or 0, per_goal(b0, "e_attr_j") or 0,
            sum(r["success"] for r in b0), len(b0)))
    summary = {}
    for energy in ("e_node_dyn_j", "e_attr_j"):
        for tokens in ("tok_uncached", "tok_billed"):
            lines, outcome = decide(table, energy, tokens, a.boot, rng)
            summary[(energy, tokens)] = outcome
            rep += ["", "## %s vs %s" % (energy, tokens), "",
                    "| cell | tasks | energy winner | token winner | CI EpG(tok) minus EpG(en), J | result |",
                    "|---|---|---|---|---|---|"]
            for cell, nt, pe, pt, ci, tag in lines:
                cis = "%.1f to %.1f" % ci if ci else ""
                rep.append("| %s %s | %d | %s | %s | %s | %s |" % (cell[0], cell[1], nt, pe, pt, cis, tag))
    rep += ["", "## Per cell values (EpG node_dyn J, TpG uncached, success)", "",
            "| cell | " + " | ".join(POLICIES) + " |", "|---|" + "---|" * len(POLICIES)]
    for cell in sorted(table):
        vals = []
        for p in POLICIES:
            rs = table[cell].get(p, [])
            e, t = per_goal(rs, "e_node_dyn_j"), per_goal(rs, "tok_uncached")
            vals.append("%s / %s / %d of %d" % ("%.0f" % e if e else "na", "%.0f" % t if t else "na",
                                                sum(r["success"] for r in rs), len(rs)))
        rep.append("| %s %s | %s |" % (cell[0], cell[1], " | ".join(vals)))
    rep += pooled_early(table, rng, a.boot)
    strong = "DISAGREE" in summary[("e_node_dyn_j", "tok_uncached")] or \
        "DISAGREE" in summary[("e_attr_j", "tok_uncached")]
    weak = any("DISAGREE" in v for v in summary.values())
    point = any("point only" in v for v in summary.values())
    verdict = "STRONG GO" if strong else "WEAK GO" if weak else "INCONCLUSIVE" if point else "NO GO"
    rep[1:1] = ["", "Decision (design 9): **%s**" % verdict]
    path = os.path.join(out_dir, "s7_report.md")
    open(path, "w").write("\n".join(rep) + "\n")
    print("\n".join(rep))
    print("\nwrote", path)


if __name__ == "__main__":
    main()
