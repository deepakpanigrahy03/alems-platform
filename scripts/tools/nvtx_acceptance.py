#!/usr/bin/env python3
"""
nvtx_acceptance.py  45.4d / 45.8 I acceptance on a real capture (read only).

Criteria (AUDIT_45.8 section I):
  1. check_nvtx_spans.py status 'agree' for the run (NVTX range == stored span, within tolerance)
  2. CUDA kernels of the workload fall inside the live phase NVTX ranges
  3. the post hoc span builder emits no NVTX range: none starts after the run's task end (t1)

Usage: python nvtx_acceptance.py --nsys REPORT.sqlite --store STORE.db --run-id N
"""
import argparse
import sqlite3
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument("--nsys", required=True)
p.add_argument("--store", required=True)
p.add_argument("--run-id", type=int, required=True)
a = p.parse_args()

ok = True
print("== 1. span agreement (check_nvtx_spans.py)")
r = subprocess.run([sys.executable, "scripts/tools/check_nvtx_spans.py", "--nsys", a.nsys,
                    "--store", a.store, "--run-id", str(a.run_id)], capture_output=True, text=True)
print(r.stdout.strip()[-1500:] or r.stderr.strip()[-1500:])
import re as _re
m = _re.search(r"(\d+) matched, (\d+) outside", r.stdout)
agree = bool(m) and int(m.group(1)) > 0 and int(m.group(2)) == 0
ok &= agree

n = sqlite3.connect(f"file:{a.nsys}?mode=ro", uri=True)
tables = {t[0] for t in n.execute("SELECT name FROM sqlite_master WHERE type='table'")}
kt = "CUPTI_ACTIVITY_KIND_KERNEL"
if "NVTX_EVENTS" not in tables:
    sys.exit("STOP: NVTX_EVENTS missing in the report (capture without --trace=nvtx?)")
txt = "COALESCE(e.text, s.value)" if "StringIds" in tables else "e.text"
join = "LEFT JOIN StringIds s ON s.id = e.textId" if "StringIds" in tables else ""
ranges = n.execute(f"SELECT e.start, e.end, {txt} FROM NVTX_EVENTS e {join} "
                   "WHERE e.end IS NOT NULL AND e.end > e.start").fetchall()
phase = [(s, e, t) for s, e, t in ranges if t and any(k in t for k in ("planning", "execution", "synthesis"))]
print(f"\n== 2. kernels inside live phase ranges  (NVTX ranges {len(ranges)}, phase ranges {len(phase)})")
if kt not in tables:
    print("  no CUDA kernels in the capture: the workload ran no GPU code in this process "
          "(server side engine); criterion 2 needs an in process GPU provider (llama_cpp)")
    ok = False
else:
    kern = n.execute(f"SELECT start, end FROM {kt}").fetchall()
    inside = sum(1 for ks, ke in kern if any(s <= ks and ke <= e for s, e, _ in phase))
    print(f"  kernels {len(kern)}  inside a phase range {inside}")
    ok &= inside > 0

print("\n== 3. no NVTX range from the post hoc builder (after t1)")
st = sqlite3.connect(f"file:{a.store}?mode=ro", uri=True)
row = st.execute("SELECT start_time_ns, pre_task_duration_ns, task_duration_ns FROM runs WHERE run_id = ?",
                 (a.run_id,)).fetchone()
if not row or None in row:
    print("  run window unknown in the store")
    ok = False
else:
    # nsys timestamps are relative to the session start; align with the first phase range
    first_phase = min((s for s, _, _ in phase), default=None)
    first_store = st.execute("SELECT MIN(start_time_ns) FROM orchestration_events WHERE run_id = ? "
                             "AND phase IN ('planning','execution','synthesis')", (a.run_id,)).fetchone()[0]
    if first_phase is None or first_store is None:
        print("  cannot align clocks (no phase range or no stored phase)")
        ok = False
    else:
        off = first_store - first_phase
        t1 = row[0] + row[1] + row[2]
        late = [(t, (s + off - t1) / 1e6) for s, e, t in ranges if s + off > t1 + 1_000_000]
        print(f"  ranges starting more than 1 ms after t1: {len(late)}")
        for t, ms in late[:5]:
            print(f"    {t}  +{ms:.1f} ms")
        ok &= not late

print("\nACCEPTED" if ok else "\nNOT ACCEPTED")
sys.exit(0 if ok else 1)
