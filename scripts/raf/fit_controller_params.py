"""RAF R5: fit controller parameters from the export, evaluate offline, write outputs.

  python fit_controller_params.py --export export_dir --out out_dir --tag e1+e1b

Inputs  (export_recovery_store.py): goals.csv (one row per goal and attempt), calls.csv.
Outputs: controller_params.json (R6 layout), r5_numbers.json (R4), CONTROLLER_REPORT.md,
         xstar_vs_ratio.png, flip_vs_work.png.

Decision key = observable class (coordinator Oct 16). Work done = total calls
(model plus tool) completed in attempt 1. predict_energy returns the expected
TOTAL energy of the goal (sunk attempt 1 plus remainder), so J = E / p is energy
per achieved goal. No joules of attempt 1, task id or early/late enter a decision.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from alems_plugin_bfcl.decider import predict as P
except ImportError:
    import predict as P  # flat layout on HPRC
import controller_model as cm  # noqa: E402

PARAMS_VERSION = "r5-1"
PRIOR_CAP = 10.0          # effective observations kept in the prior (prior_scale)
RESTART, RESUME = "FR_warm", "SR_warm"
SEED = 0

# ------------------------------------------------------------------ data

def goal_table(goals_csv):
    g = pd.read_csv(goals_csv)
    g = g.sort_values(["goal_id", "attempt"])
    a1 = g[g.attempt == 1].set_index("goal_id")
    pa = g.pivot_table(index="goal_id", columns="attempt", values="pass_fail", aggfunc="last")
    agg = g.groupby("goal_id").agg(epg=("e_board_j", "sum"), attempts=("attempt", "max"),
                                   ok=("pass_fail", "last"))
    cols = ["task", "cls", "point", "option", "fired_phase", "calls_total", "calls_model",
            "last_prompt_tokens", "e_board_j"] + (["tok_uncached_rest"] if "tok_uncached_rest" in a1.columns else [])
    t = a1[cols].join(agg)
    t["ok2"] = pa.get(2); t["ok3"] = pa.get(3)
    t = t.rename(columns={"calls_total": "W", "calls_model": "Wm",
                          "last_prompt_tokens": "tok", "e_board_j": "e_a1"})
    b0 = t[t.option == "B0"].groupby("task").agg(L=("W", "median"), C=("epg", "median"))
    t = t[t.option != "B0"].join(b0, on="task")
    t["side"] = t.fired_phase.map({"llm": "model", "tool": "tool"}).fillna("other")
    t["ok"] = t.ok.fillna(0).astype(int)
    t["tok"] = t.tok.fillna(t.tok.median())
    return t.reset_index()

# ------------------------------------------------------------------ fitting

def _line(s):
    """epg = a + b W + g tok (least squares); mean if too few or degenerate."""
    if len(s) < 6 or s.W.nunique() < 2:
        return dict(e2_a=float(s.epg.mean()), e2_b=0.0, e2_g=0.0, e2_h=0.0)
    X = np.column_stack([np.ones(len(s)), s.W, s.tok / 1e4])
    th = np.linalg.lstsq(X, s.epg.values, rcond=None)[0]
    return dict(e2_a=float(th[0]), e2_b=float(th[1]), e2_g=float(th[2] / 1e4), e2_h=0.0)


def _tok_line(s):
    """uncached prompt tokens to finish = tok_a + tok_b * calls_done."""
    if "tok_uncached_rest" not in s or s.tok_uncached_rest.notna().sum() < 3:
        return dict(tok_a=None, tok_b=None)
    u = s.dropna(subset=["tok_uncached_rest"])
    if u.W.nunique() < 2:
        return dict(tok_a=float(u.tok_uncached_rest.mean()), tok_b=0.0)
    b, a = np.polyfit(u.W, u.tok_uncached_rest, 1)
    return dict(tok_a=float(a), tok_b=float(b))


def _beta(k, n):
    k, n = int(k), int(n)
    s = min(1.0, PRIOR_CAP / n) if n else 1.0
    return dict(alpha=float(1 + k * s), beta=float(1 + (n - k) * s), alpha_raw=1 + k, beta_raw=1 + n - k,
                n=int(n), k=int(k))


def fit(t):
    classes = {}
    for key, s in list(t.groupby("cls")) + [("_default", t)]:
        opts = {}
        for o, so in s.groupby("option"):
            d = _line(so)
            d.update(_beta(so.ok.sum(), len(so)))
            rp = so[(so.ok2 == 0) & so.ok3.notna()] if "ok3" in so else so.iloc[0:0]
            d["p_repeat_after_fail"] = float((rp.ok3.sum() + 1) / (len(rp) + 2)) if len(rp) >= 3 else None
            d["n_repeat"] = int(len(rp))
            d.update(_tok_line(so))
            opts[o] = d
        classes[key] = {"options": opts}
    params = {"classes": classes, "overhead": None, "prior_scale": PRIOR_CAP,
              "params_version": PARAMS_VERSION, "data_tag": None}
    for key, s in list(t.groupby("cls")) + [("_default", t)]:
        c = params["classes"][key]
        js = [P.expected_J(params, key, o, s.W.median(), s.tok.median(), 0) for o in c["options"]]
        js = [j for j in js if j is not None]
        c["_budget_j"] = 2.0 * min(js) if js else None
    return params

# ------------------------------------------------------------------ x* (6.8, D5)

def _tok_of_W(s):
    if s.W.nunique() < 2:
        return lambda w: float(s.tok.median())
    b, a = np.polyfit(s.W, s.tok, 1)
    return lambda w: a + b * w


def xstar_point(params, cls, s, restart=RESTART, resume=RESUME):
    tokf = _tok_of_W(s)
    grid = np.arange(0, max(s.W.max(), 1) + 1.01, 0.1)
    for w in grid:
        jr = P.expected_J(params, cls, restart, w, tokf(w), 0)
        js = P.expected_J(params, cls, resume, w, tokf(w), 0)
        if jr is None or js is None:
            return None
        if js <= jr:
            return float(w)
    return float(grid[-1])


def xstar_table(t, params, n_boot=300):
    rng = np.random.default_rng(SEED)
    out = {}
    for cls, s in t.groupby("cls"):
        x = xstar_point(params, cls, s)
        if x is None:
            continue
        tasks = s.task.unique()
        draws = []
        for _ in range(n_boot):
            pick = rng.choice(tasks, len(tasks), replace=True)
            sb = pd.concat([s[s.task == k] for k in pick])
            pb = fit(sb)
            xb = xstar_point(pb, cls, sb)
            if xb is not None:
                draws.append(xb)
        ratio_m = float((s.Wm / s.W.replace(0, np.nan)).median())
        jpc = float((s.e_a1 / s.W.replace(0, np.nan)).median())
        o = params["classes"][cls]["options"]
        out[cls] = dict(x_star_calls=x,
                        x_star_calls_ci=[float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))] if draws else None,
                        x_star_calls_model=x * ratio_m, x_star_joules=x * jpc,
                        p_restart=o[RESTART]["alpha"] / (o[RESTART]["alpha"] + o[RESTART]["beta"]),
                        p_resume=o[RESUME]["alpha"] / (o[RESUME]["alpha"] + o[RESUME]["beta"]),
                        W_range=[float(s.W.min()), float(s.W.max())])
        params["classes"][cls].update(_x_star_calls=x, _x_star_calls_ci=out[cls]["x_star_calls_ci"],
                                      _x_star_calls_model=out[cls]["x_star_calls_model"],
                                      _x_star_joules=out[cls]["x_star_joules"])
    return out

# ------------------------------------------------------------------ flip test (D5)

def flip_test(t):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss
    key = ["task", "cls", "point"]
    a = t[t.option == RESTART].set_index(key)
    b = t[t.option == RESUME].set_index(key)
    m = a.join(b, lsuffix="_r", rsuffix="_s", how="inner")
    jr = np.where(m.ok_r > 0, m.epg_r, np.inf)
    js = np.where(m.ok_s > 0, m.epg_s, np.inf)
    m = m[np.isfinite(jr) | np.isfinite(js)].copy()
    jr = np.where(m.ok_r > 0, m.epg_r, np.inf); js = np.where(m.ok_s > 0, m.epg_s, np.inf)
    m["restart_wins"] = (jr < js).astype(int)
    m["W"] = m.W_r; m["frac"] = m.W_r / m.L_r
    with np.errstate(invalid="ignore", divide="ignore"):
        m["saving_pct"] = np.where(np.isfinite(jr) & np.isfinite(js), 100 * (js - jr) / js, np.nan)
    m = m.reset_index()
    res = {}
    for name, cols in {"work_calls": ["W"], "fraction": ["frac"], "both": ["W", "frac"]}.items():
        pr, ys = [], []
        for task in m.task.unique():
            tr, te = m[m.task != task], m[m.task == task]
            if tr.restart_wins.nunique() < 2:
                p = np.full(len(te), tr.restart_wins.mean())
            else:
                p = LogisticRegression().fit(tr[cols], tr.restart_wins).predict_proba(te[cols])[:, 1]
            pr += list(p); ys += list(te.restart_wins)
        pr = np.clip(pr, 1e-3, 1 - 1e-3)
        res[name] = dict(accuracy=float(np.mean((pr > .5) == np.array(ys))),
                         log_loss=float(log_loss(ys, pr, labels=[0, 1])))
    for col, lab in [("W", "calls"), ("frac", "fraction")]:
        vals = np.unique(m[col])
        cand = np.concatenate([[vals.min() - 1], (vals[:-1] + vals[1:]) / 2, [vals.max() + 1]])
        best = min(((float(c), int(((m[col] < c).astype(int) != m.restart_wins).sum())) for c in cand),
                   key=lambda x: x[1])
        res[f"single_threshold_{lab}"] = dict(threshold=best[0], misclassified=best[1], blocks=len(m))
    return res, m

# ------------------------------------------------------------------ CV regret

def _realized(block, opt):
    r = block[block.option == opt]
    return (None, None) if r.empty else (float(r.epg.iloc[0]), int(r.ok.iloc[0]))


def cross_validate(t, folds=5):
    rng = np.random.default_rng(SEED)
    tasks = np.array(sorted(t.task.unique())); rng.shuffle(tasks)
    options = sorted(t.option.unique())
    rows = []
    for test in np.array_split(tasks, folds):
        tr, te = t[~t.task.isin(test)], t[t.task.isin(test)]
        pr = fit(tr)
        xs = {c: xstar_point(pr, c, s) for c, s in tr.groupby("cls")}
        gb = _gbr(tr, options)
        for key, blk in te.groupby(["task", "cls", "point"]):
            cls = key[1]
            W, tok = float(blk.W.median()), float(blk.tok.median())
            choice, _ = P.decide(pr, cls, options, W, tok, 0)
            succ = blk[blk.ok > 0]
            oracle = (succ if len(succ) else blk).sort_values("epg").option.iloc[0]
            x = xs.get(cls)
            picks = {"model": choice, "oracle": oracle,
                     "always_SR_warm": RESUME, "always_FR_warm": RESTART,
                     "always_RB_cold": "RB_cold",
                     "xstar_rule": (RESTART if (x is not None and W < x) else RESUME),
                     "gbr": gb(cls, W, tok)}
            for meth, o in picks.items():
                if o == "abandon":
                    e, ok = float(blk.e_a1.median()), 0
                else:
                    e, ok = _realized(blk, o)
                    if e is None:
                        continue
                rows.append(dict(task=key[0], cls=cls, point=key[2], method=meth, option=o, epg=e, ok=ok))
    r = pd.DataFrame(rows)
    s = r.groupby("method").agg(blocks=("ok", "size"), energy=("epg", "sum"), succ=("ok", "sum"))
    s["EpS"] = s.energy / s.succ
    s["regret_pct"] = 100 * (s.EpS / s.loc["oracle", "EpS"] - 1)
    s["success"] = s.succ / s.blocks
    s["succ_ci"] = [cm.wilson(k, n) for k, n in zip(s.succ, s.blocks)]
    return s.sort_values("EpS"), r


def _gbr(tr, options):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    cls_list = sorted(tr.cls.unique())
    def X(cls, opt, W, tok):
        return [W, tok] + [cls == c for c in cls_list] + [opt == o for o in options]
    Xt = np.array([X(r.cls, r.option, r.W, r.tok) for r in tr.itertuples()], float)
    reg = HistGradientBoostingRegressor(max_iter=200, random_state=SEED).fit(Xt, tr.epg)
    clf = HistGradientBoostingClassifier(max_iter=200, random_state=SEED).fit(Xt, tr.ok) \
        if tr.ok.nunique() > 1 else None
    def choose(cls, W, tok):
        Xo = np.array([X(cls, o, W, tok) for o in options], float)
        e = reg.predict(Xo)
        p = clf.predict_proba(Xo)[:, 1] if clf is not None else np.ones(len(options))
        return options[int(np.argmin(e / np.maximum(p, 1e-3)))]
    return choose

# ------------------------------------------------------------------ plots

def plots(t, params, xs, flip_m, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4.2, 3))
    for cls, s in t.groupby("cls"):
        if cls not in xs:
            continue
        o = params["classes"][cls]["options"]
        base_beta = dict(o[RESUME])
        ratios = np.linspace(0.6, 1.0, 21)
        pr = xs[cls]["p_restart"]
        ys = []
        for rr in ratios:
            ps = rr * pr
            tmp = copy.deepcopy(params)
            tmp["classes"][cls]["options"][RESUME].update(alpha=ps * 1000, beta=(1 - ps) * 1000)
            ys.append(xstar_point(tmp, cls, s))
        ax.plot(ratios, ys, label=cls)
        params["classes"][cls]["options"][RESUME] = base_beta
    ax.set_xlabel("p_resume / p_restart"); ax.set_ylabel("x* (calls done)")
    ax.legend(fontsize=6); fig.tight_layout()
    fig.savefig(os.path.join(out, "xstar_vs_ratio.png"), dpi=200); plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.2, 3))
    sc = ax.scatter(flip_m.W, flip_m.saving_pct, c=flip_m.L_r, s=12, cmap="viridis")
    ax.axhline(0, lw=.5, c="k"); fig.colorbar(sc, label="task length (calls)")
    ax.set_xlabel("calls done at failure (model plus tool)")
    ax.set_ylabel("restart saving vs resume, % of J")
    fig.tight_layout(); fig.savefig(os.path.join(out, "flip_vs_work.png"), dpi=200); plt.close(fig)

# ------------------------------------------------------------------ main

def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(1 << 24), b""):
            h.update(ch)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--boot", type=int, default=300)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    gp, cp = os.path.join(a.export, "goals.csv"), os.path.join(a.export, "calls.csv")
    t = goal_table(gp)
    params = fit(t)

    calls = pd.read_csv(cp).dropna(subset=["context_tokens", "e_board_j"])
    law = cm.fit_energy_law(calls.context_tokens, calls.e_board_j, calls.task, n_boot=a.boot)
    lin = cm.fit_energy_law(calls.context_tokens, calls.e_board_j, None, n_boot=0, quadratic=False)
    params["energy_law"] = dict(a=law.a, b=law.b, d=law.d, ci=law.ci, n=law.n,
                                linear_a=lin.a, linear_b=lin.b)

    xs = xstar_table(t, params, a.boot)
    flip, flip_m = flip_test(t)
    cv, cv_rows = cross_validate(t)
    plots(t, params, xs, flip_m, a.out)

    try:
        git = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                                      stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        git = "nogit"
    meta = json.load(open(os.path.join(a.export, "export_meta.json"))) \
        if os.path.exists(os.path.join(a.export, "export_meta.json")) else {}
    prov = dict(data_tag=a.tag, git=git, goals_sha256=_sha(gp), calls_sha256=_sha(cp),
                db_sha256=meta.get("db_sha256"), ledgers=meta.get("ledgers"),
                goals=int(t.goal_id.nunique()), unit_calls="total calls (model plus tool)")
    params["provenance"] = prov
    params["data_tag"] = a.tag
    json.dump(params, open(os.path.join(a.out, "controller_params.json"), "w"), indent=1)

    sr = cv.loc["always_SR_warm", "EpS"]; fr = cv.loc["always_FR_warm", "EpS"]
    md = cv.loc["model", "EpS"]
    numbers = dict(provenance=prov,
                   ctrlSavingVsResume=float(100 * (sr - md) / sr),
                   ctrlSavingVsRestart=float(100 * (fr - md) / fr),
                   ctrlRegret=float(cv.loc["model", "regret_pct"]),
                   gbrRegret=float(cv.loc["gbr", "regret_pct"]),
                   xstarRuleRegret=float(cv.loc["xstar_rule", "regret_pct"]),
                   xstar=xs,
                   energyLaw=params["energy_law"],
                   betaPosterior={c: {o: {k: v[k] for k in ("alpha", "beta", "alpha_raw", "beta_raw", "n", "k")}
                                      for o, v in d["options"].items()}
                                  for c, d in params["classes"].items()},
                   flip=flip)
    json.dump(numbers, open(os.path.join(a.out, "r5_numbers.json"), "w"), indent=1)

    L = [f"# Controller report ({a.tag})", "",
         f"Goals {prov['goals']}; work done unit: {prov['unit_calls']}; energy: board DC_INPUT dynamic.", "",
         "## Cross validation by task (5 folds): energy per success", "",
         cv.drop(columns=["succ_ci"]).round(3).to_markdown(), "",
         "## x* per class (restart FR_warm below, resume SR_warm above)", ""]
    for c, v in xs.items():
        L.append(f"* {c}: x* = {v['x_star_calls']:.1f} calls, CI {v['x_star_calls_ci']}, "
                 f"model calls {v['x_star_calls_model']:.1f}, {v['x_star_joules']:.0f} J; "
                 f"p_resume/p_restart = {v['p_resume'] / v['p_restart']:.2f}; W range {v['W_range']}")
    L += ["", "## Flip test across task lengths", "", "```", json.dumps(flip, indent=1), "```", "",
          "## Energy law e(c) per call (board, dynamic)", "",
          f"e(c) = {law.a:.4g} + {law.b:.4g} c + {law.d:.4g} c^2; CI {law.ci}; n = {law.n}",
          f"linear: {lin.a:.4g} + {lin.b:.4g} c", "",
          "## Pending", "", "p_repeat_after_fail and overhead O need persistent cells (E2)."]
    open(os.path.join(a.out, "CONTROLLER_REPORT.md"), "w").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
