#!/usr/bin/env python3
"""
R6 smoke gates (coordinator Oct 16 13:20, item 3). Read only.

    python scripts/raf/controller_smoke_check.py --store <experiments.db> --after <annotation_id>
        [--params <controller_params.json>]   (absent: stub params)

Checks every raf.controller decision with annotation_id > --after:
  G1 anchored on the span of attempt n-1 of the same goal
  G2 population, counts, seed, sampled p per option recorded
  G3 recompute equals recorded: counts from the store at the recorded cut_id,
     and the seeded samples from those counts (beta_p decisions only)
  G4 abandon stops the goal: abandoned attempt has no run and no later attempt
  G5 decider latency logged (ns per decision)
  G6 no I/O inside windows: structural (hook before t0, write after the goal);
     reported, not measurable from the store
Exit 0 only if G1 to G5 pass.
"""

import argparse
import json
import sqlite3
import statistics
import sys

from core.execution.raf_history import POP_KEYS, read_history

ROWS = ("SELECT a.annotation_id, a.span_id, a.payload, ga.goal_id, ga.attempt_number "
        "FROM span_annotations a LEFT JOIN goal_attempt ga ON ga.span_id = a.span_id "
        "WHERE a.annotation_type = 'raf.controller' AND a.annotation_id > ? "
        "ORDER BY a.annotation_id")


def _beta(params_path):
    from alems_plugin_bfcl.decider.beta_p import BetaP
    from alems_plugin_bfcl.decider.stubs import stub_params
    return BetaP.from_file(params_path) if params_path else BetaP(stub_params())


def _state(p, prev):
    from alems_plugin_bfcl.decider.base import FailureState
    return FailureState(failure_class=p["failure_class"], attempt=int(p["decision_attempt"]),
                        calls_done=int(p.get("calls_done") or 0), tokens_context=None,
                        tokens_cached=None, joules_done=None, work_source=p["work_source"],
                        turns_done=0, turn_starts=(), previous_options=tuple(prev))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--after", type=int, default=0)
    ap.add_argument("--params", default=None)
    a = ap.parse_args()
    conn = sqlite3.connect("file:%s?mode=ro" % a.store, uri=True)
    from alems_plugin_bfcl.decider.base import OPTIONS
    beta = _beta(a.params)
    fails, ns, by_goal = [], [], {}
    rows = list(conn.execute(ROWS, (a.after,)))
    for aid, span, raw, goal_id, att_n in rows:
        p = json.loads(raw)
        tag = "annotation %d goal %s attempt %s" % (aid, p.get("goal_id"), p.get("decision_attempt"))
        # G1
        if goal_id != p.get("goal_id") or att_n != int(p["decision_attempt"]) - 1:
            fails.append("G1 " + tag)
        # G2
        need = list(POP_KEYS) + ["counts", "seed", "scores", "cut_id", "option"]
        missing = [k for k in need if k not in p]
        if missing or any(s.get("p") is None for s in p.get("scores", [])):
            fails.append("G2 %s missing %s" % (tag, missing))
            continue
        # G3 counts
        pop = {k: p[k] for k in POP_KEYS}
        again = read_history(conn, pop, cut_id=p["cut_id"])["counts"]
        if again != p["counts"]:
            fails.append("G3 counts %s recorded %s recomputed %s" % (tag, p["counts"], again))
        # G3 samples (beta_p only; laya_p p comes from the server)
        prev = [o for (_, o) in sorted(by_goal.get(goal_id, []))]
        if p.get("decider_used") == "beta_p" and p.get("params_version") == beta.version:
            d = beta.decide(_state(p, prev), list(OPTIONS), counts=p["counts"], seed=p["seed"])
            got = {s.option: s.p for s in d.scores}
            rec = {s["option"]: s["p"] for s in p["scores"]}
            if got != rec or d.option != p["option"]:
                fails.append("G3 samples %s" % tag)
        by_goal.setdefault(goal_id, []).append((int(p["decision_attempt"]), p["option"]))
        # G5
        if not isinstance(p.get("ns"), int) or p["ns"] <= 0:
            fails.append("G5 " + tag)
        else:
            ns.append(p["ns"])
    # G4
    abandoned = 0
    for goal_id, decs in by_goal.items():
        last_att, last_opt = max(decs)
        if last_opt != "ABANDON":
            continue
        abandoned += 1
        r = conn.execute("SELECT failure_type, run_id FROM goal_attempt "
                         "WHERE goal_id=? AND attempt_number=?", (goal_id, last_att)).fetchone()
        later = conn.execute("SELECT COUNT(*) FROM goal_attempt WHERE goal_id=? "
                             "AND attempt_number>?", (goal_id, last_att)).fetchone()[0]
        # The goal tracker stores -1 as "no run"; an abandoned attempt must have
        # no runs row at all, whatever the marker.
        runs_rows = 0 if r is None else conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id = ?", (r[1],)).fetchone()[0]
        if r is None or r[0] != "abandoned" or r[1] not in (None, -1) or runs_rows or later:
            fails.append("G4 goal %s: %s later=%s" % (goal_id, r, later))
    print("decisions %d, goals %d, abandoned goals %d" % (len(rows), len(by_goal), abandoned))
    if ns:
        print("decider latency ms: median %.3f max %.3f" % (statistics.median(ns) / 1e6, max(ns) / 1e6))
    cuts = [json.loads(r[2]).get("cut_id") for r in rows]
    print("cut_id per decision:", cuts)
    print("G6: structural (pre attempt hook before t0; decisions written after execute_goal)")
    if abandoned == 0:
        fails.append("G4 no abandoned goal in the smoke")
    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else "FAILED %d" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
