"""RAF R5 export: read only snapshot of the A-LEMS store -> goals.csv, calls.csv.

Never run against the live store on GN100; use the snapshot copied to HPRC.

  python export_recovery_store.py --db raf_e1_e1b_snapshot.db \
      --ledger L1.jsonl --ledger L2.jsonl ... --out export_dir

Energy rules (Addendum D3):
  board  = DC_INPUT (domain 28), values in mW per sample: J = sum(mW/1000 * interval_s)
           (equals mean times duration); dynamic = total minus idle W of the run's
           baseline (v_idle_baseline_domains via runs.baseline_id) times duration.
  pkg    = PACKAGE domain, energy per interval (summed), idle subtracted the same way.
  gpu    = gpu_samples with source dcgm, summed.
Success = output_quality.pass_fail of the attempt (judge bfcl_state); final attempt
gives goal success. Goals restricted to the ledgers given (latest record rule).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from recovery_features import load_ledgers  # noqa: E402

BOARD_DOMAIN = 28


def q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def domain_names(con):
    cols = [r[1] for r in con.execute("pragma table_info('energy_domains')")]
    idc = "domain_id" if "domain_id" in cols else cols[0]
    namec = next((c for c in cols if "name" in c), cols[1])
    d = q(con, f"select {idc} as domain_id, {namec} as name from energy_domains")
    return dict(zip(d.domain_id, d.name))


def in_list(ids):
    return ",".join(str(int(i)) for i in ids)


def export(db, ledgers, out):
    os.makedirs(out, exist_ok=True)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    led = load_ledgers(ledgers)
    gids = sorted(set(led.goal_id))
    names = domain_names(con)
    board_name = names.get(BOARD_DOMAIN)
    pkg_id = next((k for k, v in names.items() if str(v).upper() == "PACKAGE"), None)

    att = q(con, f"""
        select a.attempt_id, a.goal_id, a.run_id, a.attempt_number as attempt,
               a.failure_type, a.started_at_ns, a.finished_at_ns,
               g.task_id,
               r.baseline_id, r.start_time_ns, r.end_time_ns, r.duration_ns,
               r.llm_calls, r.tool_calls, r.steps, r.total_tokens, r.prompt_tokens
        from goal_attempt a
        join goal_execution g on g.goal_id = a.goal_id
        join runs r on r.run_id = a.run_id
        where a.goal_id in ({in_list(gids)})""")
    runs = sorted(set(att.run_id))

    oq = q(con, f"""select attempt_id, pass_fail, judge_method from output_quality
                    where goal_id in ({in_list(gids)})""")
    oq = oq[oq.judge_method.astype(str).str.contains("bfcl", case=False, na=False)] \
        if oq.judge_method.astype(str).str.contains("bfcl", case=False).any() else oq
    oq = oq.drop_duplicates("attempt_id", keep="last")[["attempt_id", "pass_fail"]]

    inj = q(con, f"""select attempt_id, injected_type, target_step, target_phase
                     from failure_injection_log where goal_id in ({in_list(gids)})
                     and status = 'injected'""")
    inj = inj.drop_duplicates("attempt_id", keep="first")

    # samples: board and package per sample, with time window
    smp = q(con, f"""
        select d.run_id, d.domain_id, d.energy_uj, s.timestamp_ns, s.interval_ns
        from energy_sample_domains d join energy_samples_v2 s on s.sample_id = d.sample_id
        where d.run_id in ({in_list(runs)}) and d.domain_id in ({BOARD_DOMAIN}{',' + str(pkg_id) if pkg_id else ''})""")
    smp["dt_s"] = smp.interval_ns / 1e9
    smp["J"] = np.where(smp.domain_id == BOARD_DOMAIN,
                        smp.energy_uj / 1000.0 * smp.dt_s,   # mW to J over interval
                        smp.energy_uj / 1e6)                 # uJ per interval
    idle = q(con, "select baseline_id, domain_name, power_watts from v_idle_baseline_domains")
    idle_board = idle[idle.domain_name == board_name].set_index("baseline_id").power_watts
    idle_pkg = idle[idle.domain_name.astype(str).str.upper() == "PACKAGE"].set_index("baseline_id").power_watts

    per_run = smp.groupby(["run_id", "domain_id"]).J.sum().unstack(fill_value=np.nan)
    att["dur_s"] = att.duration_ns / 1e9
    att["e_board_total_j"] = att.run_id.map(per_run.get(BOARD_DOMAIN, pd.Series(dtype=float)))
    att["e_board_j"] = att.e_board_total_j - att.baseline_id.map(idle_board).fillna(np.nan) * att.dur_s
    if pkg_id:
        att["e_pkg_j"] = att.run_id.map(per_run[pkg_id]) - att.baseline_id.map(idle_pkg) * att.dur_s

    gpu = q(con, f"""select run_id, sum(energy_uj)/1e6 as e_gpu_j from gpu_samples
                     where source='dcgm' and run_id in ({in_list(runs)}) group by run_id""")
    att = att.merge(gpu, on="run_id", how="left").merge(oq, on="attempt_id", how="left") \
             .merge(inj, on="attempt_id", how="left")

    # per call records
    calls = q(con, f"""select run_id, step_index, prompt_tokens, completion_tokens,
                       request_start_ns, last_token_time_ns, total_time_ms, prefill_energy_uj
                       from llm_interactions where run_id in ({in_list(runs)})
                       order by run_id, step_index""")
    # last_token_time_ns is not populated in this schema version: end = start + total_time_ms
    calls["end_ns"] = calls.last_token_time_ns.where(
        calls.last_token_time_ns.notna(), calls.request_start_ns + calls.total_time_ms * 1e6)
    b = smp[smp.domain_id == BOARD_DOMAIN].sort_values(["run_id", "timestamp_ns"])
    bys = {r: g for r, g in b.groupby("run_id")}
    run_base = att.set_index("run_id").baseline_id.map(idle_board)
    ej = []
    for r in calls.itertuples():
        g = bys.get(r.run_id)
        if g is None or pd.isna(r.request_start_ns) or pd.isna(r.end_ns):
            ej.append(np.nan); continue
        m = (g.timestamp_ns >= r.request_start_ns) & (g.timestamp_ns <= r.end_ns)
        dur = (r.end_ns - r.request_start_ns) / 1e9
        ej.append(g.J[m].sum() - run_base.get(r.run_id, 0.0) * dur if m.any() else np.nan)
    calls["e_board_j"] = ej
    calls["context_tokens"] = calls.prompt_tokens

    last = calls.groupby("run_id").prompt_tokens.last().rename("last_prompt_tokens")
    att = att.merge(last, on="run_id", how="left")

    lk = led.set_index("goal_id")
    for c in ["task", "cls", "plan_cls", "point", "option", "family", "persistent", "tok_uncached_rest",
              "fired_step", "fired_phase", "study"]:
        att[c] = att.goal_id.map(lk[c])
    att["calls_model"] = att.llm_calls
    att["calls_total"] = att.llm_calls.fillna(0) + att.tool_calls.fillna(0)
    att["turns"] = att.steps
    att["tok_billed"] = att.total_tokens
    att["tok_uncached"] = np.nan          # not stored per attempt in this schema
    att["cls_observed"] = att.failure_type.where(att.failure_type.notna(), att.injected_type)

    goals_cols = ["goal_id", "task", "cls", "plan_cls", "point", "option", "family",
                  "persistent", "study", "attempt", "attempt_id", "run_id", "baseline_id",
                  "e_board_j", "e_pkg_j", "e_gpu_j", "dur_s", "calls_total", "calls_model",
                  "turns", "last_prompt_tokens", "tok_billed", "tok_uncached", "pass_fail",
                  "cls_observed", "fired_step", "fired_phase", "target_step", "tok_uncached_rest"]
    att = att[[c for c in goals_cols if c in att.columns]].sort_values(["goal_id", "attempt"])
    calls = calls.merge(att[["run_id", "goal_id", "task", "attempt"]], on="run_id", how="left")
    calls = calls.rename(columns={"step_index": "call_index"})

    att.to_csv(os.path.join(out, "goals.csv"), index=False)
    calls.to_csv(os.path.join(out, "calls.csv"), index=False)
    meta = dict(db=db, db_sha256=_sha(db), ledgers={p: _sha(p) for p in ledgers},
                goals=len(gids), attempts=len(att), calls=len(calls),
                board_domain=board_name, pkg_domain=pkg_id,
                missing_board=int(att.e_board_j.isna().sum()),
                missing_pass=int(att.pass_fail.isna().sum()))
    json.dump(meta, open(os.path.join(out, "export_meta.json"), "w"), indent=1)
    print(json.dumps(meta, indent=1))


def _sha(p, n=1 << 24):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(n):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--ledger", action="append", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    export(a.db, a.ledger, a.out)
