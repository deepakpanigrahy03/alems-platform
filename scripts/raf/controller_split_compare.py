#!/usr/bin/env python3
"""
RAF R6 split run check (coordinator item 11), read only.

Compares the controller decisions of a single invocation (study A) with the
same goals run as several invocations (study B). Both studies use the same
profile apart from study_id, so goal order and Thompson seeds are identical.

    python scripts/raf/controller_split_compare.py --store <db> --single <single_run_study_id> --split <three_runs_study_id> [--arm <arm>]

Criteria (proposed to the coordinator, Oct 16):
  (a) recorded counts equal the store recompute at each decision's cut_id
  (b) evidence carries across invocations: once any earlier goal of the study
      has an outcome, the decision's counts are not empty
  (c) choices match decision by decision up to the first goal whose outcome
      differs; later differences are reported, not failed
Exit 0 when (a) and (b) hold for both studies and (c) holds up to divergence.
"""

import argparse
import json
import sqlite3
import sys

from core.execution.raf_history import POP_KEYS, read_history

ROWS = ("SELECT a.annotation_id, a.payload FROM span_annotations a "
        "WHERE a.annotation_type = 'raf.controller' ORDER BY a.annotation_id")
OUTCOME = ("SELECT MAX(pass_fail) FROM goal_attempt "
           "WHERE goal_id = ? AND pass_fail IS NOT NULL")


def decisions(conn, study, arm):
    """Decisions of one study and arm, ordered by goal then decision attempt."""
    out = []
    for _aid, raw in conn.execute(ROWS):
        p = json.loads(raw)
        if p.get("study_id") == study and (arm is None or p.get("arm") == arm):
            out.append(p)
    out.sort(key=lambda p: (p["goal_id"], p["decision_attempt"]))
    return out


def outcome(conn, goal_id):
    r = conn.execute(OUTCOME, (goal_id,)).fetchone()
    return None if r is None or r[0] is None else int(r[0])


def check_study(conn, decs, name, fails):
    """Criteria (a) and (b) for one study."""
    goals_seen, scored = [], False
    for p in decs:
        if p["goal_id"] not in goals_seen:
            if goals_seen and outcome(conn, goals_seen[-1]) is not None:
                scored = True
            goals_seen.append(p["goal_id"])
        pop = {k: p[k] for k in POP_KEYS}
        again = read_history(conn, pop, cut_id=p["cut_id"])["counts"]
        if again != p["counts"]:
            fails.append("(a) %s goal %s attempt %s" % (name, p["goal_id"], p["decision_attempt"]))
        if scored and not p["counts"]:
            fails.append("(b) %s goal %s attempt %s: empty counts after scored goals"
                         % (name, p["goal_id"], p["decision_attempt"]))
    return goals_seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--single", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--arm", default=None)
    a = ap.parse_args()
    conn = sqlite3.connect("file:%s?mode=ro" % a.store, uri=True)
    fails = []
    da, db = decisions(conn, a.single, a.arm), decisions(conn, a.split, a.arm)
    ga = check_study(conn, da, a.single, fails)
    gb = check_study(conn, db, a.split, fails)
    print("decisions single %d split %d, goals single %d split %d"
          % (len(da), len(db), len(ga), len(gb)))
    # (c): align by goal rank and decision attempt; stop at the first outcome divergence.
    rank_a = {g: i for i, g in enumerate(ga)}
    rank_b = {g: i for i, g in enumerate(gb)}
    ka = {(rank_a[p["goal_id"]], p["decision_attempt"]): p for p in da}
    kb = {(rank_b[p["goal_id"]], p["decision_attempt"]): p for p in db}
    matched, diverged_at = 0, None
    for i in range(min(len(ga), len(gb))):
        steps = sorted(k for k in ka if k[0] == i)
        if steps != sorted(k for k in kb if k[0] == i):
            diverged_at = i
            break
        for k in steps:
            if ka[k]["option"] != kb[k]["option"]:
                fails.append("(c) goal rank %d attempt %d: %s vs %s"
                             % (i, k[1], ka[k]["option"], kb[k]["option"]))
            matched += 1
        if outcome(conn, ga[i]) != outcome(conn, gb[i]):
            diverged_at = i + 1
            break
    print("(c) decisions matched before divergence: %d; first diverging goal rank: %s"
          % (matched, diverged_at if diverged_at is not None else "none"))
    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else "FAILED %d" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
