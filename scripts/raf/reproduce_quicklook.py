"""Reproduce the coordinator's E1/E1b quick look from export goals.csv.

  python reproduce_quicklook.py --goals export_e1_e1b/goals.csv

Per (point, side, option): goals, successes (final attempt pass_fail),
mean EpG (board, all attempts) and J = EpG / success rate.
Side: model = failure fired in the llm phase, tool = tool phase.
"""
import argparse

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--goals", required=True)
a = ap.parse_args()

g = pd.read_csv(a.goals)
g = g[g.option != "B0"]
per = g.sort_values("attempt").groupby("goal_id").agg(
    point=("point", "first"), phase=("fired_phase", "first"), option=("option", "first"),
    cls=("cls", "first"), epg=("e_board_j", "sum"), ok=("pass_fail", "last"))
per["side"] = per.phase.map({"llm": "model", "tool": "tool"}).fillna(per.phase)

t = per.groupby(["point", "side", "option"]).agg(
    n=("ok", "size"), k=("ok", "sum"), epg=("epg", "mean")).reset_index()
t["p"] = t.k / t.n
t["J"] = t.epg / t.p.where(t.p > 0)
pd.set_option("display.width", 160)
for (pt, sd), s in t.groupby(["point", "side"]):
    s = s.sort_values("J")
    best = s.iloc[0]
    print(f"\n== {pt} / {sd}: lowest J = {best.option} "
          f"({(s.iloc[1].J / best.J - 1) * 100:.1f}% below next, {s.iloc[1].option})")
    print(s[["option", "n", "k", "epg", "J"]].round(1).to_string(index=False))

print("\n== per class, goals and successes per option")
print(per.pivot_table(index=["cls", "point"], columns="option", values="ok",
                      aggfunc=lambda x: f"{int(x.sum())}/{len(x)}").to_string())
