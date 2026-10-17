#!/usr/bin/env python3
"""
RAF E3 analysis: oracle in hindsight, controller regret, per cell tables,
learning curve (coordinator definitions, Oct 17). Read only.

    python scripts/raf/raf_e3_oracle_regret.py --store <experiments.db> \
        --ledgers <dir>/raf_e3_items_*_ledger.jsonl --out <dir>

Definitions:
  arm      decider arms by name (ctrl_beta, ctrl_laya, xstar_rule, token_rule);
           fixed arms by policy (FR_warm, SR_warm, RB_cold)
  goal     last valid ledger record per (block, arm); invalid finals are excluded
  energy   goal_execution.total_energy_uj of the goal (joules)
  success  MAX(goal_attempt.pass_fail) of the goal
  oracle   per block, lowest energy among arms whose goal passed: the best
           fixed choice in hindsight among the paired arms, not a global optimum
  regret   per block and controller arm, controller energy minus oracle energy,
           on blocks where every arm has a valid goal and the controller passed;
           controller failures on blocks with an oracle are counted separately
  EpG      total energy over successes, per arm, on the paired blocks
Points appear only in the analysis tables, never in any decision.
"""

import argparse
import csv
import glob
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

CONTROLLERS = ("ctrl_beta", "ctrl_laya")
ENERGY = "SELECT total_energy_uj FROM goal_execution WHERE goal_id = ?"
PASS = "SELECT MAX(pass_fail) FROM goal_attempt WHERE goal_id = ? AND pass_fail IS NOT NULL"


def arm_label(rec):
    arm = rec.get("arm") or rec["plan"].get("arm")
    return rec["plan"]["policy"] if arm in (None, "fixed") else arm


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def load_goals(paths, conn):
    """(block, arm) -> goal dict, from the last record (highest rerun) per pair."""
    last = {}
    for path in paths:
        with open(path) as fh:
            for line in fh:
                rec = json.loads(line)
                if rec["plan"].get("policy") == "B0":
                    continue
                key = (rec["item_id"], arm_label(rec))
                if key not in last or rec["rerun"] >= last[key]["rerun"]:
                    last[key] = rec
    goals = {}
    for key, rec in last.items():
        e = conn.execute(ENERGY, (rec["goal_id"],)).fetchone()
        p = conn.execute(PASS, (rec["goal_id"],)).fetchone()
        goals[key] = {"block": key[0], "arm": key[1], "goal_id": rec["goal_id"],
                      "position": rec["position"], "cls": rec["plan"]["cls"],
                      "point": rec["plan"]["point"], "valid": not rec["validity"],
                      "codes": rec["validity"],
                      "energy_j": None if not e or e[0] is None else e[0] / 1e6,
                      "passed": None if not p or p[0] is None else bool(p[0]),
                      "abandoned": "abandoned" in (rec.get("detail") or {})}
    return goals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--ledgers", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=int, default=20)
    a = ap.parse_args()
    paths = sorted({p for g in a.ledgers for p in glob.glob(g)})
    conn = sqlite3.connect("file:%s?mode=ro" % a.store, uri=True)
    goals = load_goals(paths, conn)
    arms = sorted({g["arm"] for g in goals.values()})
    blocks = defaultdict(dict)
    for g in goals.values():
        blocks[g["block"]][g["arm"]] = g
    os.makedirs(a.out, exist_ok=True)

    rows, excluded, no_oracle, ctrl_fail = [], 0, 0, defaultdict(int)
    for bid, by_arm in sorted(blocks.items(), key=lambda kv: min(g["position"] for g in kv[1].values())):
        if set(by_arm) != set(arms) or not all(g["valid"] and g["energy_j"] is not None
                                               and g["passed"] is not None for g in by_arm.values()):
            excluded += 1
            continue
        passed = {arm: g["energy_j"] for arm, g in by_arm.items() if g["passed"]}
        any_g = next(iter(by_arm.values()))
        row = {"block": bid, "cls": any_g["cls"], "point": any_g["point"],
               "position": min(g["position"] for g in by_arm.values())}
        if not passed:
            no_oracle += 1
            row["oracle_arm"], row["oracle_j"] = "", ""
        else:
            oarm = min(sorted(passed), key=lambda k: passed[k])
            row["oracle_arm"], row["oracle_j"] = oarm, round(passed[oarm], 3)
        for arm, g in by_arm.items():
            row[arm + "_j"] = round(g["energy_j"], 3)
            row[arm + "_pass"] = int(g["passed"])
            if arm in CONTROLLERS and passed:
                if g["passed"]:
                    row[arm + "_regret_j"] = round(g["energy_j"] - row["oracle_j"], 3)
                else:
                    ctrl_fail[arm] += 1
        rows.append(row)

    cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("block", "cls", "point", "position", "oracle_arm", "oracle_j"), k))
    with open(os.path.join(a.out, "e3_blocks.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    lines = ["# E3 oracle and regret", "",
             "Paired blocks %d, excluded %d (an arm missing, invalid, or without energy or outcome), "
             "blocks without a passing arm %d (no oracle, excluded from regret)."
             % (len(rows), excluded, no_oracle), "",
             "Oracle = best fixed choice in hindsight among the paired arms, not a global optimum.", "",
             "| Arm | Goals | Success | 95% CI | EpG J | Oracle wins |", "|---|---|---|---|---|---|"]
    wins = defaultdict(int)
    for r in rows:
        if r["oracle_arm"]:
            wins[r["oracle_arm"]] += 1
    for arm in arms:
        k = sum(r[arm + "_pass"] for r in rows)
        e = sum(r[arm + "_j"] for r in rows)
        lo, hi = wilson(k, len(rows))
        lines.append("| %s | %d | %d | %s | %s | %d |" % (
            arm, len(rows), k, "%.2f to %.2f" % (lo, hi) if lo is not None else "",
            "%.1f" % (e / k) if k else "", wins[arm]))
    for arm in CONTROLLERS:
        reg = [r[arm + "_regret_j"] for r in rows if arm + "_regret_j" in r]
        if not reg and not ctrl_fail[arm]:
            continue
        orc = [r["oracle_j"] for r in rows if arm + "_regret_j" in r]
        lines += ["", "## Regret, %s" % arm,
                  "Blocks with regret %d: mean %.2f J, median %.2f J, mean %.1f %% of oracle; "
                  "controller failed on %d blocks that had an oracle."
                  % (len(reg), sum(reg) / len(reg), sorted(reg)[len(reg) // 2],
                     100 * sum(reg) / sum(orc), ctrl_fail[arm]) if reg else
                  "No passed controller goals; failed on %d oracle blocks." % ctrl_fail[arm],
                  "", "Learning curve (mean regret J per %d blocks in queue order):" % a.window]
        seq = [r[arm + "_regret_j"] for r in rows if arm + "_regret_j" in r]
        for i in range(0, len(seq), a.window):
            part = seq[i:i + a.window]
            lines.append("- blocks %d to %d: %.2f" % (i + 1, i + len(part), sum(part) / len(part)))
    lines += ["", "## Per class and point", "", "| Class | Point | Arm | Success | EpG J |", "|---|---|---|---|---|"]
    cells = defaultdict(list)
    for r in rows:
        cells[(r["cls"], r["point"])].append(r)
    for (cls, pt), rs in sorted(cells.items()):
        for arm in arms:
            k = sum(r[arm + "_pass"] for r in rs)
            e = sum(r[arm + "_j"] for r in rs)
            lines.append("| %s | %s | %s | %d of %d | %s |" % (cls, pt, arm, k, len(rs),
                                                            "%.1f" % (e / k) if k else ""))
    report = "\n".join(lines) + "\n"
    with open(os.path.join(a.out, "e3_oracle_regret.md"), "w") as fh:
        fh.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
