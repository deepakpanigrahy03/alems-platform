#!/usr/bin/env python3
"""
Build an --items file for rerunning specific goals of a scheduled study.

A goal is selected when its id is listed (--goals) or when its goal row was
created inside a time window (--from, --to, UTC, as stored). Each selected
goal maps, through the study ledger, to its queue item and policy; the
output has one line per goal: item id, a tab, policy. Read only: the store
is opened read only and the ledger is never written.

Usage:
    python scripts/raf/make_rerun_items.py --ledger <run_ledger.jsonl> --store <experiments.db> \
        --goals 512,513 --from "2026-10-15 22:05:00" --to "2026-10-15 22:16:00" --out <file.tsv>
"""

import argparse
import json
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--goals", default="")
    ap.add_argument("--from", dest="t0", default="")
    ap.add_argument("--to", dest="t1", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    wanted = {int(g) for g in a.goals.split(",") if g}
    if a.t0 and a.t1:
        db = sqlite3.connect("file:%s?mode=ro" % a.store, uri=True)
        wanted |= {r[0] for r in db.execute(
            "SELECT goal_id FROM goal_execution WHERE created_at BETWEEN ? AND ?", (a.t0, a.t1))}
    by_goal = {}
    with open(a.ledger) as fh:
        for line in fh:
            r = json.loads(line)
            by_goal[int(r["goal_id"])] = (r["item_id"], r["plan"]["policy"])
    lines, unknown = [], []
    for g in sorted(wanted):
        if g in by_goal:
            lines.append("%s\t%s\t# goal %d" % (by_goal[g][0], by_goal[g][1], g))
        else:
            unknown.append(g)      # not a goal of this study (for example a smoke)
    with open(a.out, "w") as fh:
        fh.write("# rerun items for %s\n" % a.ledger)
        for l in lines:
            item, pol, comment = l.split("\t")
            fh.write("%s\t%s\n" % (item, pol))
    print("selected %d goals of the study; not in the study: %s" % (len(lines), unknown))
    for l in lines:
        print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
