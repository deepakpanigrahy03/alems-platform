"""RAF R5 features: one row per recovery goal (attempt 1 failure event).

Sources (never the store; Addendum D1):
  E1/E1b: run ledger JSONL plus the shared read only export (EXPORT_SPEC_R5.md).
  Pilot:  artifacts/raf_pilot/s7_goals.csv, smoke only (success = completion).

Usage:
  python recovery_features.py probe  --ledger data/raf_e1_run_ledger.jsonl
  python recovery_features.py pilot  --csv s7_goals.csv --out features_pilot.csv
  python recovery_features.py export --goals goals.csv [--ledger L.jsonl ...] \
         --tag e1+e1b --out features.csv

Work done counts: W_calls_total = model plus tool calls (primary, D2 item 2);
W_calls_model = model calls only (reported alongside).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys

import numpy as np
import pandas as pd

FEATURE_COLS = [
    "goal_id", "task", "cls", "point", "option", "depth", "cache",
    "W_calls_total", "W_calls_model", "W_joules", "W_tokens", "turns_done",
    "L_calls_total", "L_calls_model", "L_joules",
    "E2_j", "tok2_billed", "tok2_uncached",
    "attempts", "epg_j", "success_final", "success_a2",
    "data_tag", "smoke",
]
CONTAMINATING = {"poison", "wrong_state", "hallucinated_result"}  # E2 classes, extend from ledger


def norm_task(t: str) -> str:
    """D3 d: ledger names carry 'bfcl_' prefix; task lists do not."""
    return re.sub(r"^bfcl_(?=multi_turn)", "", str(t))


def split_option(opt: str):
    depth, _, cache = str(opt).partition("_")
    return depth, cache or "warm"

# --------------------------------------------------------------- probe

def probe(path: str, n: int = 3) -> None:
    keys = collections.Counter()
    rows = []
    with open(path) as f:
        for i, line in enumerate(f):
            r = json.loads(line)
            keys.update(_flat_keys(r))
            if i < n:
                rows.append(r)
    print(f"rows read: {i + 1}")
    for k, v in sorted(keys.items()):
        print(f"{v:6d}  {k}")
    for r in rows:
        print(json.dumps(r)[:1500])


def _flat_keys(d, pre=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat_keys(v, f"{pre}{k}.")
        else:
            yield f"{pre}{k}"

# --------------------------------------------------------------- pilot

def from_pilot(csv: str) -> pd.DataFrame:
    """Smoke features. Failure position is not in the CSV: early is taken as
    one third and late as two thirds of the B0 call count of the task.
    Numbers from this path are never reported (D4 item 4)."""
    d = pd.read_csv(csv)
    d["task"] = d["task"].map(norm_task)
    b0 = d[d.policy == "B0"].groupby("task").agg(L_calls=("calls", "median"),
                                                 L_j=("e_node_dyn_j", "median"))
    r = d[d.policy != "B0"].join(b0, on="task")
    frac = r["point"].map({"early": 1 / 3, "late": 2 / 3}).astype(float)
    out = pd.DataFrame({
        "goal_id": r.goal_id, "task": r.task, "cls": r.cls, "point": r.point,
        "option": r.policy,
        "W_calls_total": np.round(frac * r.L_calls), "W_calls_model": np.round(frac * r.L_calls),
        "W_joules": frac * r.L_j, "W_tokens": np.nan, "turns_done": np.nan,
        "L_calls_total": r.L_calls, "L_calls_model": r.L_calls, "L_joules": r.L_j,
        "E2_j": r.e_node_dyn_j - frac * r.L_j,
        "tok2_billed": r.tok_billed, "tok2_uncached": r.tok_uncached,
        "attempts": 2, "epg_j": r.e_node_dyn_j,
        "success_final": r.success, "success_a2": r.success,
        "data_tag": "pilot_smoke", "smoke": 1,
    })
    out["depth"], out["cache"] = zip(*out.option.map(split_option))
    return out[FEATURE_COLS]

# --------------------------------------------------------------- export

def from_export(goals_csv: str, ledgers: list[str], tag: str) -> pd.DataFrame:
    """goals_csv follows EXPORT_SPEC_R5.md (one row per goal and attempt).
    Ledgers supply class, point, option and the planned failure call index
    when the export lacks them; join on goal_id."""
    g = pd.read_csv(goals_csv)
    g["task"] = g["task"].map(norm_task)
    if ledgers:
        led = pd.concat([pd.json_normalize([json.loads(x) for x in open(p)])
                         for p in ledgers], ignore_index=True)
        led = led.rename(columns=lambda c: c.split(".")[-1])
        keep = [c for c in ["goal_id", "cls", "point", "option", "fail_call_index"]
                if c == "goal_id" or (c in led.columns and c not in g.columns)]
        g = g.merge(led[keep].drop_duplicates("goal_id"), on="goal_id", how="left")
    a1 = g[g.attempt == 1].set_index("goal_id")
    a2 = g[g.attempt == 2].set_index("goal_id")
    allatt = g.groupby("goal_id").agg(attempts=("attempt", "max"), epg_j=("e_board_j", "sum"))
    final = g.sort_values("attempt").groupby("goal_id").last()
    b0 = g[(g.option == "B0") & (g.attempt == 1)].groupby("task").agg(
        L_calls_total=("calls_total", "median"), L_calls_model=("calls_model", "median"),
        L_joules=("e_board_j", "median"))
    ids = a2.index
    out = pd.DataFrame({
        "goal_id": ids, "task": a1.loc[ids, "task"].values,
        "cls": a1.loc[ids, "cls"].values, "point": a1.loc[ids, "point"].values,
        "option": a1.loc[ids, "option"].values,
        "W_calls_total": a1.loc[ids, "calls_total"].values,
        "W_calls_model": a1.loc[ids, "calls_model"].values,
        "W_joules": a1.loc[ids, "e_board_j"].values,
        "W_tokens": a1.loc[ids, "last_prompt_tokens"].values,
        "turns_done": a1.loc[ids, "turns"].values,
        "E2_j": a2.loc[ids, "e_board_j"].values,
        "tok2_billed": a2.loc[ids, "tok_billed"].values,
        "tok2_uncached": a2.loc[ids, "tok_uncached"].values,
        "attempts": allatt.loc[ids, "attempts"].values,
        "epg_j": allatt.loc[ids, "epg_j"].values,
        "success_final": final.loc[ids, "pass_fail"].values,
        "success_a2": a2.loc[ids, "pass_fail"].values,
        "data_tag": tag, "smoke": 0,
    })
    out = out.join(b0, on="task")
    out["depth"], out["cache"] = zip(*out.option.map(split_option))
    return out[FEATURE_COLS]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe", "pilot", "export"])
    ap.add_argument("--ledger", action="append", default=[])
    ap.add_argument("--csv"); ap.add_argument("--goals")
    ap.add_argument("--tag", default="e1"); ap.add_argument("--out", default="features.csv")
    a = ap.parse_args(argv)
    if a.mode == "probe":
        for p in a.ledger:
            probe(p)
        return
    df = from_pilot(a.csv) if a.mode == "pilot" else from_export(a.goals, a.ledger, a.tag)
    df.to_csv(a.out, index=False)
    print(f"wrote {a.out}: {len(df)} goals, tag {df.data_tag.iloc[0]}", file=sys.stderr)


if __name__ == "__main__":
    main()
