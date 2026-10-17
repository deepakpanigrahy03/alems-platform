"""Add coordinator requested fields (Oct 17 05:15) to r5_numbers.json, with CIs.

  python augment_r5_numbers.py --main <data>/e2 --clean <data>

  --main : folder holding export_e1_e1b_e2/ and r5_e1_e1b_e2/ (tag e1_e1b_e2)
  --clean: folder holding export_e1_e1b/ and r5_e1_e1b/ (sub tag e1_e1b)
CIs: 95% percentile bootstrap resampling tasks (1000 draws), seed 0.
Cross validation is recomputed with the same seed and folds as the fit.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_controller_params as F  # noqa: E402
import controller_model as cm  # noqa: E402

B = 1000
rng = np.random.default_rng(0)


def eps(rows):
    s = rows.groupby("method").agg(e=("epg", "sum"), k=("ok", "sum"), n=("ok", "size"))
    return s.e / s.k, s.k / s.n


def boot(rows, fn):
    tasks = rows.task.unique()
    by = {t: g for t, g in rows.groupby("task")}
    vals = []
    for _ in range(B):
        r = pd.concat([by[t] for t in rng.choice(tasks, len(tasks), replace=True)])
        try:
            vals.append(fn(r))
        except (KeyError, ZeroDivisionError):
            pass
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def gain(a, b):
    """percent by which method b's EpS is below method a's: 100 (a - b) / a."""
    def f(r):
        e, _ = eps(r)
        return float(100 * (e[a] - e[b]) / e[a])
    return f


def same_outcome(goals_csv):
    g = pd.read_csv(goals_csv)
    g = g[g.option != "B0"]
    s = g.groupby(["task", "cls", "point", "option", "attempt"]).pass_fail.first().reset_index()
    out = {}
    for name, (a, b) in {"sameOutcomeModel": ("json_parse", "rate_limit"),
                         "sameOutcomeTool": ("timeout", "tool_error")}.items():
        m = s[s.cls == a].merge(s[s.cls == b], on=["task", "point", "option", "attempt"])
        m["same"] = (m.pass_fail_x == m.pass_fail_y).astype(float)
        f = lambda r: float(100 * r.same.mean())
        out[name] = dict(value=f(m), ci=boot(m, f), n=int(len(m)), sub_tag="e1_e1b")
    return out


def linear_law_ci(calls_csv):
    c = pd.read_csv(calls_csv).dropna(subset=["context_tokens", "e_board_j"])
    def fit(r):
        b, a = np.polyfit(r.context_tokens, r.e_board_j, 1)
        return a, b
    a0, b0 = fit(c)
    tasks = c.task.unique(); by = {t: g for t, g in c.groupby("task")}
    A, Bv = [], []
    for _ in range(B):
        r = pd.concat([by[t] for t in rng.choice(tasks, len(tasks), replace=True)])
        a, b = fit(r); A.append(a); Bv.append(b)
    pc = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    return dict(linear_a=float(a0), linear_a_ci=pc(A), linear_b=float(b0), linear_b_ci=pc(Bv),
                n_calls=int(len(c)), n_tasks=int(len(tasks)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True)
    ap.add_argument("--clean", required=True)
    a = ap.parse_args()
    mx, mo = os.path.join(a.main, "export_e1_e1b_e2"), os.path.join(a.main, "r5_e1_e1b_e2")
    cx, co = os.path.join(a.clean, "export_e1_e1b"), os.path.join(a.clean, "r5_e1_e1b")

    t_main = F.goal_table(os.path.join(mx, "goals.csv"))
    _, rows = F.cross_validate(t_main)
    e, sr = eps(rows)
    succ = rows.groupby("method").ok.agg(["sum", "size"])
    w = lambda m: [float(x) for x in cm.wilson(succ.loc[m, "sum"], succ.loc[m, "size"])]

    t_clean = F.goal_table(os.path.join(cx, "goals.csv"))
    _, rows_c = F.cross_validate(t_clean)
    e_c, _ = eps(rows_c)

    num_path = os.path.join(mo, "r5_numbers.json")
    num = json.load(open(num_path))
    clean_num = json.load(open(os.path.join(co, "r5_numbers.json")))
    add = {
        "ctrlSavingVsRollback": dict(value=float(100 * (e["always_RB_cold"] - e["model"]) / e["always_RB_cold"]),
                                     ci=boot(rows, gain("always_RB_cold", "model"))),
        "ctrlSavingVsResumeCI": boot(rows, gain("always_SR_warm", "model")),
        "ctrlSavingVsRestartCI": boot(rows, gain("always_FR_warm", "model")),
        "ctrlGainContam": dict(value=float(100 * (e["xstar_rule"] - e["model"]) / e["xstar_rule"]),
                               ci=boot(rows, gain("xstar_rule", "model")),
                               note="full controller vs x* rule, E1+E1b+E2"),
        "xstarRuleGainClean": dict(value=float(100 * (e_c["model"] - e_c["xstar_rule"]) / e_c["model"]),
                                   ci=boot(rows_c, gain("model", "xstar_rule")), sub_tag="e1_e1b",
                                   note="x* rule below full controller, clean failures"),
        "ctrlOfflineSucc": dict(value=float(sr["model"]), ci=w("model")),
        "frOfflineSucc": dict(value=float(sr["always_FR_warm"]), ci=w("always_FR_warm")),
        "srOfflineSucc": dict(value=float(sr["always_SR_warm"]), ci=w("always_SR_warm")),
        "nCtrlBlocks": int(succ.loc["model", "size"]),
        "nCtrlGoals": int(t_main.goal_id.nunique()),
        "workCallsAcc": num["flip"]["work_calls"]["accuracy"],
        "workFracAcc": num["flip"]["fraction"]["accuracy"],
        "workCallsAccClean": dict(value=clean_num["flip"]["work_calls"]["accuracy"], sub_tag="e1_e1b"),
        "workFracAccClean": dict(value=clean_num["flip"]["fraction"]["accuracy"], sub_tag="e1_e1b"),
        "energyLawLinear": linear_law_ci(os.path.join(mx, "calls.csv")),
        "ci_method": "95% percentile bootstrap over tasks, 1000 draws, seed 0; success CIs Wilson",
    }
    add.update(same_outcome(os.path.join(cx, "goals.csv")))
    num.update(add)
    json.dump(num, open(num_path, "w"), indent=1)
    print(json.dumps(add, indent=1))
    print("updated", num_path)


if __name__ == "__main__":
    main()
