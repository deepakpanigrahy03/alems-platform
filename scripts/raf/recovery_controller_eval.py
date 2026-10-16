"""RAF R5 offline evaluation: cross validation by task, x* in work done,
flip consistency across task lengths, GBR baseline, outputs for R4.

  python recovery_controller_eval.py --features features.csv [--calls calls.csv] \
         --out out_dir [--folds 5] [--smoke]

Rerun on new data = rerun recovery_features.py then this; one command each.
Outputs: CONTROLLER_OFFLINE_REPORT.md, controller_params.json,
r5_numbers.json (D8), xstar_vs_ratio.png, flip_vs_work.png.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import controller_model as cm  # noqa: E402

RESTART, RESUME = "FR", "SR"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if path else None


def git_rev():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=os.path.dirname(__file__) or ".",
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "nogit"

# ------------------------------------------------------------- per block truth

def block_table(df):
    """Measured J per (task, cls, point, option) = mean EpG / smoothed success."""
    g = df.groupby(["task", "cls", "point", "option"]).agg(
        n=("success_final", "size"), k=("success_final", "sum"),
        epg=("epg_j", "mean"), E2=("E2_j", "mean"), W=("W_joules", "mean"),
        Wc=("W_calls_total", "mean"), Wm=("W_calls_model", "mean"),
        L=("L_calls_total", "first"), Lm=("L_calls_model", "first"),
        LJ=("L_joules", "first"), tokU=("tok2_uncached", "mean")).reset_index()
    g["p"] = [cm.p_smoothed(k, n) for k, n in zip(g.k, g.n)]
    g["J"] = g.epg / g.p
    return g


def oracle(bt):
    i = bt.groupby(["task", "cls", "point"]).J.idxmin()
    return bt.loc[i].set_index(["task", "cls", "point"])

# ------------------------------------------------------------- fitting

def fit_params(train, prior_bank=None):
    bank = cm.BetaBank()
    if prior_bank is not None:
        bank.ab.update(prior_bank.ab)
    e2 = {}
    for (c, o), s in train.groupby(["cls", "option"]):
        for y in s.success_final:
            bank.update((c, o), bool(y))
        e2[(c, o)] = cm.fit_e2(s.W_joules, s.E2_j)
    # repeat failure: attempt 3 given attempt 2 failed (persistent cells)
    rep = train[(train.attempts >= 3)]
    for (c, o), s in rep.groupby(["cls", "option"]):
        for y in s.success_final:
            bank.update((c, o, "repeat"), bool(y))
    budget = 2 * float(np.median(train.W_joules + train.E2_j))
    return bank, e2, budget


def theta_fit(train, bt_train):
    """Pick theta (calls) minimizing summed J of the history free rule."""
    best, bestJ = None, np.inf
    for th in np.arange(0, train.W_calls_total.max() + 2):
        tot = 0.0
        for _, r in bt_train.groupby(["task", "cls", "point"]):
            o = cm.history_free_rule(r.Wc.iloc[0], th, False)
            m = r[r.option == o]
            tot += (m.J.iloc[0] if len(m) else r.J.max())
        if tot < bestJ:
            best, bestJ = float(th), tot
    return best

# ------------------------------------------------------------- GBR baseline

def gbr_choose(train, test_blocks, options):
    from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
    feats = ["W_calls_total", "W_joules", "W_tokens"]

    def X(df, opt_col):
        Z = pd.get_dummies(df[["cls"]].assign(option=opt_col).astype(str))
        Z = pd.concat([df[feats].reset_index(drop=True), Z.reset_index(drop=True)], axis=1)
        return Z.reindex(columns=cols, fill_value=0).fillna(-1).astype(float)

    cols = feats + sorted(pd.get_dummies(train[["cls", "option"]].astype(str)).columns)
    Xt = X(train, train.option.values)
    reg = HistGradientBoostingRegressor(max_iter=200, random_state=0).fit(Xt, train.E2_j)
    ys = train.success_final.astype(int)
    clf = (HistGradientBoostingClassifier(max_iter=200, random_state=0).fit(Xt, ys)
           if ys.nunique() > 1 else None)
    choice = {}
    for key, r in test_blocks.groupby(["task", "cls", "point"]):
        row = r.iloc[[0]]
        best, bj = None, np.inf
        for o in options:
            Xo = X(row, [o])
            e2 = float(reg.predict(Xo)[0])
            p = float(clf.predict_proba(Xo)[0, 1]) if clf is not None else 1.0
            j = e2 / max(p, 1e-3)
            if j < bj:
                best, bj = o, j
        choice[key] = best
    return choice

# ------------------------------------------------------------- CV

def cross_validate(df, folds=5, seed=0):
    tasks = np.array(sorted(df.task.unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(tasks)
    parts = np.array_split(tasks, min(folds, len(tasks)))
    bt = block_table(df)
    orc = oracle(bt)
    options = sorted(df.option.unique())
    rows = []
    for test_tasks in parts:
        tr, te = df[~df.task.isin(test_tasks)], df[df.task.isin(test_tasks)]
        bt_tr = block_table(tr)
        bank, e2, B = fit_params(tr)
        th = theta_fit(tr, bt_tr)
        tokbest = tr.groupby(["cls", "option"]).tok2_uncached.mean()
        gb = gbr_choose(tr, te, options)
        for key, r in te.groupby(["task", "cls", "point"]):
            c = key[1]
            W = r.W_joules.mean()
            preds = {o: e2[(c, o)][0] + e2[(c, o)][1] * W for o in options if (c, o) in e2}
            m_choice, _ = cm.decide(c, preds, bank)
            tk = tokbest.loc[c].idxmin() if c in tokbest.index.get_level_values(0) else "SR_warm"
            choices = {
                "model": m_choice,
                "rule": cm.history_free_rule(r.W_calls_total.mean(), th, False),
                "always_SR_warm": "SR_warm", "always_FR_warm": "FR_warm",
                "token_uncached": tk, "gbr": gb[key], "oracle": orc.loc[key].option,
            }
            blk = bt[(bt.task == key[0]) & (bt.cls == c) & (bt.point == key[2])].set_index("option")
            for meth, o in choices.items():
                if o not in blk.index:
                    continue
                b = blk.loc[o]
                rows.append(dict(task=key[0], cls=c, point=key[2], method=meth, option=o,
                                 J=b.J, E2=b.E2, k=b.k, n=b.n, theta=th,
                                 J_oracle=orc.loc[key].J))
    res = pd.DataFrame(rows)
    res["regret"] = res.J - res.J_oracle
    res["regret_pct"] = 100 * res.regret / res.J_oracle
    return res


def summarize(res):
    out = []
    for m, s in res.groupby("method"):
        k, n = s.k.sum(), s.n.sum()
        lo, hi = cm.wilson(k, n)
        out.append(dict(method=m, blocks=len(s), mean_J=s.J.mean(), mean_E2=s.E2.mean(),
                        regret_J=s.regret.mean(), regret_pct=s.regret_pct.mean(),
                        success=k / n, succ_lo=lo, succ_hi=hi))
    return pd.DataFrame(out).sort_values("mean_J")

# ------------------------------------------------------------- x* (6.8, D5)

def xstar_table(df, warm="warm", n_boot=1000, seed=0):
    """Per class: W* in joules, total calls, model calls, tokens; bootstrap by task."""
    rng = np.random.default_rng(seed)
    out = {}
    for c, s in df.groupby("cls"):
        def est(sub):
            fr = sub[sub.option == f"{RESTART}_{warm}"]
            sr = sub[sub.option == f"{RESUME}_{warm}"]
            if fr.empty or sr.empty:
                return None
            pr = cm.p_smoothed(fr.success_final.sum(), len(fr))
            ps = cm.p_smoothed(sr.success_final.sum(), len(sr))
            C = float(sub.L_joules.median())
            # overhead of each option beyond the clean remaining work
            Or = float((fr.E2_j - fr.L_joules).median())
            Os = float((sr.E2_j - (sr.L_joules - sr.W_joules)).median())
            Wj = cm.xstar_joules(C, pr, ps, Or, Os)
            jpc = C / float(sub.L_calls_total.median())
            jpm = C / float(sub.L_calls_model.median())
            return dict(J_per_call=jpc, O_restart=Or, O_resume=Os, W_joules=Wj, N_calls_total=Wj / jpc, N_calls_model=Wj / jpm,
                        p_restart=pr, p_resume=ps, ratio=ps / pr, C_full=C)
        point = est(s)
        if point is None:
            continue
        tasks = s.task.unique()
        draws = []
        for _ in range(n_boot):
            pick = rng.choice(tasks, len(tasks), replace=True)
            r = est(pd.concat([s[s.task == t] for t in pick]))
            if r:
                draws.append(r)
        D = pd.DataFrame(draws)
        for k in ["W_joules", "N_calls_total", "N_calls_model"]:
            point[k + "_ci"] = [float(D[k].quantile(.025)), float(D[k].quantile(.975))]
        out[c] = point
    return out


def flip_test(df, warm="warm"):
    """Does work done alone explain restart vs resume winners across task
    lengths? Compare leave one task out logistic fits on W_calls_total,
    on fraction of task done, and on both."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    bt = block_table(df)
    piv = bt[bt.option.isin([f"{RESTART}_{warm}", f"{RESUME}_{warm}"])].pivot_table(
        index=["task", "cls", "point"], columns="option", values="J")
    meta = bt.groupby(["task", "cls", "point"]).agg(Wc=("Wc", "mean"), L=("L", "first"),
                                                    WJ=("W", "mean"))
    t = piv.join(meta).dropna()
    t["restart_wins"] = (t[f"{RESTART}_{warm}"] < t[f"{RESUME}_{warm}"]).astype(int)
    t["saving_pct"] = 100 * (t[f"{RESUME}_{warm}"] - t[f"{RESTART}_{warm}"]) / t[f"{RESUME}_{warm}"]
    t["frac"] = t.Wc / t.L
    t = t.reset_index()
    res = {}
    for name, cols in {"work_calls": ["Wc"], "fraction": ["frac"], "both": ["Wc", "frac"]}.items():
        pr, ys = [], []
        for task in t.task.unique():
            tr, te = t[t.task != task], t[t.task == task]
            if tr.restart_wins.nunique() < 2:
                p = np.full(len(te), tr.restart_wins.mean())
            else:
                p = LogisticRegression().fit(tr[cols], tr.restart_wins).predict_proba(te[cols])[:, 1]
            pr += list(p); ys += list(te.restart_wins)
        pr = np.clip(pr, 1e-3, 1 - 1e-3)
        res[name] = dict(accuracy=float(np.mean((np.array(pr) > .5) == np.array(ys))),
                         log_loss=float(log_loss(ys, pr, labels=[0, 1])))
    # single threshold N on calls: best split and its error count
    best = min(((float(n), int(((t.Wc < n).astype(int) != t.restart_wins).sum()))
                for n in np.arange(0, t.Wc.max() + 2, 0.5)), key=lambda x: x[1])
    res["single_threshold_calls"] = dict(N=best[0], misclassified=best[1], blocks=len(t))
    return res, t

# ------------------------------------------------------------- plots

def plots(xs, flip_t, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ratio = np.linspace(0.5, 1.0, 51)
    fig, ax = plt.subplots(figsize=(4.2, 3))
    for c, v in xs.items():
        Ncalls = [cm.xstar_joules(v["C_full"], 1.0, r, v["O_restart"], v["O_resume"])
                  / v["J_per_call"] for r in ratio]
        ax.plot(ratio, Ncalls, label=c)
    ax.set_xlabel("p_resume / p_restart"); ax.set_ylabel("x* (calls done)")
    ax.axhline(0, lw=.5, c="k"); ax.legend(fontsize=6); fig.tight_layout()
    fig.savefig(os.path.join(out, "xstar_vs_ratio.png"), dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.2, 3))
    sc = ax.scatter(flip_t.Wc, flip_t.saving_pct, c=flip_t.L, cmap="viridis", s=14)
    ax.axhline(0, lw=.5, c="k")
    ax.set_xlabel("calls done at failure (model plus tool)")
    ax.set_ylabel("restart saving vs resume, % of J")
    fig.colorbar(sc, label="task length (calls)"); fig.tight_layout()
    fig.savefig(os.path.join(out, "flip_vs_work.png"), dpi=200); plt.close(fig)

# ------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True)
    ap.add_argument("--calls", help="per call export for e(c): context_tokens, e_board_j, task")
    ap.add_argument("--out", default="r5_out")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    df = pd.read_csv(a.features)
    tag = "+".join(sorted(df.data_tag.unique()))
    smoke = a.smoke or bool(df.smoke.any())

    law = None
    if a.calls:
        calls = pd.read_csv(a.calls)
        law = cm.fit_energy_law(calls.context_tokens, calls.e_board_j, calls.task)
        lin = cm.fit_energy_law(calls.context_tokens, calls.e_board_j, calls.task,
                                quadratic=False, n_boot=0)

    res = cross_validate(df, a.folds)
    summ = summarize(res)
    xs = xstar_table(df)
    flip, flip_t = flip_test(df)
    plots(xs, flip_t, a.out)

    bank, e2, B = fit_params(df)
    prov = dict(data_tag=tag, smoke=smoke, git=git_rev(),
                features_sha=sha(a.features), calls_sha=sha(a.calls))
    params = dict(provenance=prov, budget_B=B, theta_calls=float(res.theta.median()),
                  e2={f"{c}|{o}": dict(a=v[0], b=v[1]) for (c, o), v in e2.items()},
                  beta=bank.to_json(),
                  energy_law=None if law is None else dict(a=law.a, b=law.b, d=law.d, ci=law.ci))
    json.dump(params, open(os.path.join(a.out, "controller_params.json"), "w"), indent=1)

    sm = summ.set_index("method")
    ref = sm.loc["always_SR_warm", "mean_J"]
    numbers = dict(provenance=prov,
                   ctrlSaving=float(100 * (ref - sm.loc["model", "mean_J"]) / ref),
                   ctrlRegret=float(sm.loc["model", "regret_pct"]),
                   gbrRegret=float(sm.loc["gbr", "regret_pct"]),
                   xstar=xs,
                   betaPosterior=bank.to_json(),
                   flip=flip)
    json.dump(numbers, open(os.path.join(a.out, "r5_numbers.json"), "w"), indent=1)

    L = [f"# Controller Offline Report ({tag})", ""]
    if smoke:
        L += ["SMOKE RUN. Success is completion and failure positions are proxies.",
              "No number in this file may be reported.", ""]
    L += ["## Methods (cross validation by task)", "", summ.round(3).to_markdown(index=False), ""]
    L += ["## Threshold x* in work done", "",
          "Rule: restart if work done at failure is below N calls (model plus tool), "
          "equivalently X joules; model call count given alongside.", ""]
    for c, v in xs.items():
        L.append(f"* {c}: N = {v['N_calls_total']:.1f} calls "
                 f"[{v['N_calls_total_ci'][0]:.1f}, {v['N_calls_total_ci'][1]:.1f}], "
                 f"X = {v['W_joules']:.0f} J, model calls {v['N_calls_model']:.1f}, "
                 f"p_resume/p_restart = {v['ratio']:.2f}")
    L += ["", "## Does work done alone explain the winner across task lengths?", "",
          json.dumps(flip, indent=1), "", "Plot: flip_vs_work.png", ""]
    if law is not None:
        L += ["## Per call energy law", "",
              f"e(c) = {law.a:.3g} + {law.b:.3g} c + {law.d:.3g} c^2, CI {law.ci}",
              f"linear fit: {lin.a:.3g} + {lin.b:.3g} c", ""]
    L += ["## Honesty", "",
          "Escalation, repeat failure and overhead O need persistent cells (E2);",
          "they are reported as pending until those rows exist."]
    open(os.path.join(a.out, "CONTROLLER_OFFLINE_REPORT.md"), "w").write("\n".join(L))
    print(f"wrote {a.out} (tag {tag}, smoke {smoke})")


if __name__ == "__main__":
    main()
