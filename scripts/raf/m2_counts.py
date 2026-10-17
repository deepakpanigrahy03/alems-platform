#!/usr/bin/env python3
"""
Second model study, part 1 to part 2: write this model's own call counts and
solved flags in the sweep line format, so make_pilot_config.py applies the
E1 rule (early 2; late round(0.7 n) clamped to [3, n-1]) to them unchanged.

Inputs: the part 1 B0 ledger (one B0 goal per E1 task) and the store (read
only). Per task, from the B0 goal's final attempt run:
    llm  = LLM calls (llm_interactions rows of the run)
    tool = tool calls (attempt_meta tools sequence in the ledger)
    score = goal_attempt.pass_fail of the final attempt (BFCL checker)
Outputs:
    <out>.txt        sweep format lines: multi_turn_base_N B0 ... llm=X tool=Y ... score=S
    <out>_kept.txt   tasks with score 1 (part 2 runs only on these)
"""

import argparse
import json
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True, help="path prefix, without extension")
    a = ap.parse_args()
    db = sqlite3.connect("file:%s?mode=ro" % a.store, uri=True)
    lines, kept = [], []
    seen = set()
    runs = {}                                     # task -> [(llm, tool, score)] per repetition
    with open(a.ledger) as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    for r in reversed(rows):                      # latest record per (task, rep) wins
        task = r["plan"]["task"].replace("bfcl_", "")
        key = (task, r["plan"].get("rep"))
        if r["plan"]["policy"] != "B0" or key in seen:
            continue
        seen.add(key)
        att = db.execute("SELECT attempt_number, run_id, pass_fail FROM goal_attempt "
                         "WHERE goal_id=? ORDER BY attempt_number DESC LIMIT 1",
                         (r["goal_id"],)).fetchone()
        if att is None:
            continue
        n, run_id, passed = att
        llm = db.execute("SELECT COUNT(*) FROM llm_interactions WHERE run_id=?", (run_id,)).fetchone()[0]
        tool = len((r["attempt_meta"].get(str(n)) or {}).get("tools") or [])
        score = 1 if passed == 1 else 0
        lines.append("%s B0 m2 llm=%d tool=%d score=%d goal=%d" % (task, llm, tool, score, r["goal_id"]))
        runs.setdefault(task, []).append((llm, tool, score))
    # kept: two repetitions, both solved, identical llm and tool counts, at least 3 of each
    for task, rs in sorted(runs.items()):
        if len(rs) < 2:
            print("drop %s: %d B0 repetition(s)" % (task, len(rs)))
        elif any(s != 1 for _, _, s in rs):
            print("drop %s: not solved in every B0" % task)
        elif len(set((l, t) for l, t, _ in rs)) != 1:
            print("drop %s: unstable counts %s" % (task, [(l, t) for l, t, _ in rs]))
        elif rs[0][0] < 3 or rs[0][1] < 3:
            print("drop %s: fewer than 3 llm or 3 tool calls" % task)
        else:
            kept.append(task)
    with open(a.out + ".txt", "w") as fh:
        fh.write("\n".join(sorted(lines, key=lambda s: int(s.split()[0].rsplit("_", 1)[1]))) + "\n")
    with open(a.out + "_kept.txt", "w") as fh:
        fh.write("\n".join(sorted(kept, key=lambda t: int(t.rsplit("_", 1)[1]))) + "\n")
    print("tasks", len(runs), "B0 records", len(lines),
          "kept (both B0 solved, identical counts, at least 3 llm and 3 tool calls)", len(kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
