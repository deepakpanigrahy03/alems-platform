#!/usr/bin/env python3
"""
Read only validation of the newest runs after the workload window fix.

Usage (worktree root, venv active):
    python3 scripts/tools/validate_run_window.py [N]   # N newest runs, default 3
The same checks run per experiment in scripts/test_exp_integrity.py (check_run_window).
Opens the dev store read only. Prints one block per run, PASS / WARN / FAIL per check.
Checks that need a column the store does not have print SKIP, never fail silently.
"""
import sqlite3
import sys

DB = "/mnt/alems-data/gn100-2b96/envs/dpani/dev/alems-wt-chunk45/experiments.db"
TOL_UJ = 1000            # conservation tolerance (counter resolution is 1 mJ)
W_RANGE = (1.0, 400.0)   # plausible package power on any machine in the fleet, watts


def cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(runs)")}


def line(status, name, detail):
    print("  %-4s %-34s %s" % (status, name, detail))


def check_energy(r):
    pre, run, post = r["pre_task_energy_uj"], r["pkg_energy_uj"], r["post_task_energy_uj"]
    before, after = r["rapl_before_pretask_uj"], r["rapl_after_task_uj"]
    if None in (pre, run, post, before, after):
        line("SKIP", "conservation pre+task+post=total", "a term is NULL")
        return
    gap = pre + run + post - (after - before)
    line("PASS" if abs(gap) <= TOL_UJ else "FAIL", "conservation pre+task+post=total", "gap %d uJ" % gap)


def check_power(r):
    for name, e, d in (("pre", "pre_task_energy_uj", "pre_task_duration_ns"),
                       ("task", "pkg_energy_uj", "task_duration_ns"),
                       ("post", "post_task_energy_uj", "post_task_duration_ns")):
        if r[e] is None or not r[d]:
            line("SKIP", "%s power plausible" % name, "NULL")
            continue
        w = r[e] / r[d] * 1e3
        ok = W_RANGE[0] <= w <= W_RANGE[1]
        line("PASS" if ok else "FAIL", "%s power plausible" % name, "%.1f W over %.3f s" % (w, r[d] / 1e9))


def check_baseline(r):
    base, dyn, pkg = r["baseline_energy_uj"], r["dynamic_energy_uj"], r["pkg_energy_uj"]
    if None in (base, dyn, pkg):
        line("SKIP", "baseline + dynamic = task", "NULL")
        return
    gap = base + dyn - pkg
    line("PASS" if abs(gap) <= TOL_UJ or dyn == 0 else "WARN", "baseline + dynamic = task", "gap %d uJ" % gap)


def check_ratios(r, c):
    if {"instructions", "cycles", "ipc"} <= c and r["instructions"] and r["cycles"]:
        ipc = r["instructions"] / r["cycles"]
        stored = r["ipc"] or 0
        line("PASS" if abs(ipc - stored) < 0.01 else "WARN", "ipc = instructions / cycles",
             "%.3f vs stored %.3f" % (ipc, stored))
    else:
        line("SKIP", "ipc = instructions / cycles", "counters NULL or absent")
    for col, lo, hi in (("energy_sample_coverage_pct", 0, 100), ("cpu_fraction", 0, 1)):
        if col in c and r[col] is not None:
            ok = lo <= r[col] <= hi
            line("PASS" if ok else "FAIL", "%s in [%s, %s]" % (col, lo, hi), r[col])


def check_present(r, c):
    for col in ("gpu_total_energy_uj", "package_temp_celsius", "max_temp_c", "interrupt_rate",
                "cpu_avg_mhz", "context_switches_voluntary"):
        if col not in c:
            line("SKIP", "%s present" % col, "no such column")
        elif r[col] is None:
            line("WARN", "%s present" % col, "NULL (fine if not measured on this machine)")
        elif r[col] == 0:
            line("WARN", "%s present" % col, "0 (check: unknown must be NULL)")
        else:
            line("PASS", "%s present" % col, r[col])


def check_method(conn, run_id):
    rows = [m for (m,) in conn.execute(
        "SELECT DISTINCT method_id FROM measurement_methodology WHERE run_id=? "
        "AND metric_id IN ('task_duration_ns','pre_task_energy_uj','post_task_energy_uj')", (run_id,))]
    line("PASS" if rows == ["measurement_boundary_v2"] else "FAIL", "provenance method", rows)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    conn.row_factory = sqlite3.Row
    c = cols(conn)
    for r in conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (n,)).fetchall():
        print("\nrun %s  (exp %s, %s)" % (r["run_id"], r["exp_id"], r["workflow_type"]))
        check_energy(r)
        check_power(r)
        check_baseline(r)
        check_ratios(r, c)
        check_present(r, c)
        check_method(conn, r["run_id"])


if __name__ == "__main__":
    main()
