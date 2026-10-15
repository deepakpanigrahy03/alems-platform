#!/usr/bin/env python3
"""
RAF R2 configuration builder for E1b and E2 (SPEC_RAF_R2; coordinator
decisions P1 to P4 of 15 Oct).

E1 is fixed and never redrawn: raf_e1/e1_tasks.txt (30 tasks) and
scripts/raf/e1_gn100.yaml (late ordinals, strata). raf_e1/e3_heldout_tasks.txt
is never used.

Outputs, written to --out:
    main_gn100.yaml            families and tasks for main_schedule.py
    MAIN_TASKSET.md            selection evidence (artifact appendix)
    tasks_yaml_additions.txt   config/tasks.yaml entries for E2 tasks not in E1

Families:
    clean             E1 tasks; RB_cold, RB_warm only (E1b); E1 ordinals.
    wrong_model_step  P1: target a write by tool name (ordinal any). Early:
                      the first write in turn >= 1 whose name is not called
                      earlier in ground truth; late: the next such write in a
                      later turn. The name in every B0 run is verified by
                      the E2 B0 runs (checks on); a task whose B0 misses it
                      is dropped.
    persistent        P2: 20 seeded E1 tasks; clean ordinals (early 2, E1 late).
    silent            P3: run 1 pass set minus held out; a read in turn >= 1
                      whose output feeds a later write in the same turn.
                      P4: under 15 tasks run at 2 repetitions; under 8 the
                      family is a case study (printed, never a cell claim).

Ground truth is replayed on a fresh env; a write changes the public state
hash (the turn check's state definition). Pure file reads; no store access.
"""

import argparse
import ast
import json
import os
import random
import re
import sys
from typing import Any, Dict, List, Optional, Set

import yaml

ENGINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ENGINE, "alems-plugin-bfcl"))
from alems_plugin_bfcl.corrupt import strings_of  # noqa: E402
from alems_plugin_bfcl.env import BFCLEnv  # noqa: E402
from alems_plugin_bfcl.tasks import ground_truth, load_entry  # noqa: E402

SANDBOX = os.path.expanduser("~/sandboxes/raf-mlsys-2027")
SIX = ["FR_cold", "FR_warm", "SR_cold", "SR_warm", "RB_cold", "RB_warm"]
LINE = re.compile(r"^(?:r\d+\s+)?(multi_turn_base_\d+)\s+B0\s+.*?"
                  r"llm=(\d+)\s+tool=(\d+).*?score=(\d)")


# ── inputs ──────────────────────────────────────────────────────────────
def read_list(path: str) -> List[str]:
    with open(path) as fh:
        return [l.strip() for l in fh if l.strip()]


def r1_pass(path: str) -> Dict[str, Dict[str, int]]:
    """Run 1 pass set: score 1, at least 8 llm and 5 tool calls."""
    out = {}
    with open(path) as fh:
        for line in fh:
            m = LINE.match(line.strip())
            if m and m.group(4) == "1" and int(m.group(2)) >= 8 and int(m.group(3)) >= 5:
                out[m.group(1)] = {"llm": int(m.group(2)), "tool": int(m.group(3))}
    return out


# ── ground truth replay ─────────────────────────────────────────────────
def _arg_strings(call: str) -> Set[str]:
    node = ast.parse(call, mode="eval").body
    vals = [ast.literal_eval(a) for a in node.args]
    vals += [ast.literal_eval(kw.value) for kw in node.keywords]
    return {s for s in strings_of(vals) if len(s) > 1}


def _out_strings(text: str) -> Set[str]:
    try:
        return {s for s in strings_of(json.loads(text)) if len(s) > 1}
    except ValueError:
        return set()                       # plain text: no structured value


def replay(tid: str) -> List[Dict[str, Any]]:
    """Ground truth calls in order with turn, 1 based ordinal, write flag."""
    env = BFCLEnv(load_entry(tid))
    calls, k = [], 0
    for t, turn in enumerate(ground_truth(tid)):
        for c in turn:
            k += 1
            before = env.state_hash(public_only=True)
            out = env.call_string(c)
            calls.append({"k": k, "turn": t, "name": c.split("(", 1)[0],
                          "args": _arg_strings(c), "out": _out_strings(out),
                          "write": env.state_hash(public_only=True) != before})
    return calls


# ── targets ─────────────────────────────────────────────────────────────
def silent_target(calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    First read in turn >= 1 feeding a later write of the same turn (P3).

    An earlier call of the same name is allowed only if its output is
    identical (corrupting it first would be the same fault, not a no op).
    """
    for r in calls:
        if r["turn"] < 1 or r["write"] or not r["out"]:
            continue
        earlier = [c for c in calls if c["k"] < r["k"] and c["name"] == r["name"]]
        if any(c["out"] != r["out"] for c in earlier):
            continue
        feeds = [w for w in calls if w["turn"] == r["turn"] and w["k"] > r["k"]
                 and w["write"] and (w["args"] & r["out"])]
        if feeds:
            return {"tool_name": r["name"], "turn": r["turn"],
                    "read_k": r["k"], "write_k": feeds[0]["k"]}
    return None


def wms_target(calls: List[Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
    """P1 write names in turn >= 1, first use of the name in the task."""
    seen, names = set(), []
    for c in calls:
        if c["write"] and c["turn"] >= 1 and c["name"] not in seen:
            names.append((c["name"], c["turn"]))
        seen.add(c["name"])
    if not names:
        return None
    out = {"early": {"tool_name": names[0][0], "turn": names[0][1]}}
    late = next((n for n in names[1:] if n[1] > names[0][1]), None)
    if late:
        out["late"] = {"tool_name": late[0], "turn": late[1]}
    return out


def _num(tid: str) -> int:
    return int(tid.rsplit("_", 1)[1])


def _stratum(llm: int) -> str:
    """E1 bins (e1_gn100.yaml): short up to 10 llm, medium 11 to 13, long 14+."""
    return "short" if llm <= 10 else ("medium" if llm <= 13 else "long")


# ── build ───────────────────────────────────────────────────────────────
def families_cfg(silent_reps: int) -> Dict[str, Any]:
    return {
        "clean": {"classes": {"json_parse": "llm", "rate_limit": "llm",
                              "timeout": "tool", "tool_error": "tool"},
                  "options": ["RB_cold", "RB_warm"],
                  "points": {"early": 1, "late": 1}},
        "wrong_model_step": {"classes": {"wrong_model_step": "tool"},
                             "options": SIX, "points": {"early": 1, "late": 1},
                             "turn_check": True},
        "silent": {"classes": {"silent_tool_output": "tool"},
                   # SR_notice (A1) deferred: silent is a case study (P4, n < 8).
                   "options": SIX, "points": {"any": silent_reps},
                   "turn_check": True},
        "persistent": {"classes": {"persistent_rate_limit": "llm",
                                   "persistent_tool_error": "tool"},
                       "options": ["SR_warm", "SR_warm@escalate"],
                       "points": {"early": 1, "late": 1},
                       "max_injections": 2, "attempts": 3},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", default=SANDBOX)
    ap.add_argument("--e1-config", default=os.path.join(ENGINE, "scripts/raf/e1_gn100.yaml"))
    ap.add_argument("--out", default=".")
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--persistent", type=int, default=20)
    a = ap.parse_args()
    e1 = read_list(os.path.join(a.sandbox, "raf_e1/e1_tasks.txt"))
    held = set(read_list(os.path.join(a.sandbox, "raf_e1/e3_heldout_tasks.txt")))
    e1cfg = yaml.safe_load(open(a.e1_config))
    r1 = r1_pass(os.path.join(a.sandbox, "raf_sweep30/sweep_r1.txt"))
    assert not held & set(e1), "E1 and E3 held out overlap"

    pool = sorted((set(r1) | set(e1)) - held, key=_num)
    calls = {t: replay(t) for t in pool}
    wms = {t: wms_target(calls[t]) for t in e1}
    wms = {t: v for t, v in wms.items() if v}
    silent = {t: silent_target(calls[t]) for t in pool}
    silent = {t: v for t, v in silent.items() if v}
    rng = random.Random(a.seed)
    persist = sorted(rng.sample(sorted(e1, key=_num), min(a.persistent, len(e1))), key=_num)

    n_sil = len(silent)
    silent_reps = 2 if n_sil < 15 else 1
    tasks = {}
    for t in sorted(set(e1) | set(silent), key=_num):
        key = "bfcl_" + t
        src = e1cfg["tasks"].get(key)
        fams, tg = [], {}
        if t in e1:
            fams.append("clean")
            tg["clean"] = {"late": dict(src["late"])}
        if t in persist:
            fams.append("persistent")
            tg["persistent"] = {"early": {"llm": 2, "tool": 2}, "late": dict(src["late"])}
        if t in wms:
            fams.append("wrong_model_step")
            tg["wrong_model_step"] = {p: {"tool_name": v["tool_name"]}
                                      for p, v in wms[t].items()}
        if t in silent:
            fams.append("silent")
            tg["silent"] = {"any": {"tool_name": silent[t]["tool_name"]}}
        stratum = src["stratum"] if src else _stratum(r1[t]["llm"])
        tasks[key] = {"stratum": stratum, "families": fams, "targets": tg}

    cfg = {k: e1cfg[k] for k in ("platform", "expected_store_suffix")}
    cfg.update({"pilot": "raf_main",
                "seeds": {"block_order": 20261018, "policy_order": 20261019,
                          "model_seed": 0},
                "b0_repetitions": 3, "early_ordinal": int(e1cfg["early_ordinal"]),
                "families": families_cfg(silent_reps), "tasks": tasks})
    for k in ("isolation", "baseline", "validity"):
        cfg[k] = e1cfg[k]
    _write(a, cfg, e1, held, pool, calls, wms, silent, persist, r1)

    print("pool %d  wrong_model_step %d (late %d)  persistent %d  silent %d %s"
          % (len(pool), len(wms), sum("late" in v for v in wms.values()),
             len(persist), n_sil, sorted(silent, key=_num)))
    if n_sil < 8:
        print("P4: silent below 8 -> case study only, no cell level claim")
    elif n_sil < 15:
        print("P4: silent below 15 -> 2 repetitions per cell, state n in the paper")
    if len(wms) < 15:
        print("STOP: wrong_model_step below 15 tasks")
        return 4
    return 0


# ── output ──────────────────────────────────────────────────────────────
HEADER = ("# RAF R2 main configuration (E1b clean RB, E2 contaminating), GN100,\n"
          "# Qwen3 30B A3B Instruct 2507 FP8. Generated by scripts/raf/select_main_tasks.py;\n"
          "# do not edit by hand. Read by pilot_driver.py through main_schedule.py.\n")


def _tasks_entry(template: str, t: str, r1: Dict[str, Dict[str, int]], stratum: str) -> str:
    n = str(_num(t))
    out = template.replace("multi_turn_base_7", t).replace("base 7", "base " + n)
    out = out.replace("RAF LH 01 pilot (short)", "RAF R2 E2 (%s)" % stratum)
    out = re.sub(r"tool_calls: \d+", "tool_calls: %d" % r1[t]["tool"], out)
    return out.replace('"raf_lh_01", "short"', '"raf_r2", "%s"' % stratum)


def _write(a, cfg, e1, held, pool, calls, wms, silent, persist, r1) -> None:
    with open(os.path.join(a.out, "main_gn100.yaml"), "w") as fh:
        fh.write(HEADER)
        yaml.safe_dump(cfg, fh, sort_keys=False, default_flow_style=None, width=120)
    extra = [t for t in silent if t not in e1]
    text = open(os.path.join(ENGINE, "config/tasks.yaml")).read()
    m = re.search(r"(  - id: bfcl_multi_turn_base_7\n.*?)(?=\n  - id: |\Z)", text, re.S)
    with open(os.path.join(a.out, "tasks_yaml_additions.txt"), "w") as fh:
        for t in extra:
            if "id: bfcl_%s\n" % t in text:
                continue                   # already present, never append twice
            fh.write(_tasks_entry(m.group(1), t, r1, cfg["tasks"]["bfcl_" + t]["stratum"]) + "\n")
    md = ["# MAIN_TASKSET (RAF R2 E2; generated by scripts/raf/select_main_tasks.py)", "",
          "E1 fixed (raf_e1/e1_tasks.txt, %d tasks); E3 held out excluded (%d). "
          "Pool for silent: run 1 pass set plus E1, minus held out (%d). Seed %d." % (
              len(e1), len(held), len(pool), a.seed), "",
          "| Task | E1 | gt calls | writes | wrong_model_step early | late | persistent | silent read (turn, k to write k) |",
          "|---|---|---|---|---|---|---|---|"]
    for t in pool:
        w, s = wms.get(t) or {}, silent.get(t)
        if t not in e1 and not s:
            continue
        md.append("| %s | %s | %d | %d | %s | %s | %s | %s |" % (
            t, "yes" if t in e1 else "no", len(calls[t]),
            sum(c["write"] for c in calls[t]),
            "%s (t%d)" % (w["early"]["tool_name"], w["early"]["turn"]) if w else "",
            "%s (t%d)" % (w["late"]["tool_name"], w["late"]["turn"]) if "late" in w else "",
            "yes" if t in persist else "",
            "%s (%d, %d to %d)" % (s["tool_name"], s["turn"], s["read_k"], s["write_k"]) if s else ""))
    with open(os.path.join(a.out, "MAIN_TASKSET.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")


if __name__ == "__main__":
    sys.exit(main())
