#!/usr/bin/env python3
"""
X1 update rate measurement on NVIDIA Grace GB10 (GN100, aarch64).

Measures, per channel and condition, how often each telemetry source
delivers a sample and how often its value changes:
  * 4 SPBM energy counters (hwmon energy*_input, cumulative uJ)
  * 10 SPBM power rails (hwmon power*_input, instantaneous uW)
  * DCGM field 156 (total energy consumption, mJ) through the DCGM bindings

Conditions: IDLE (no serving process) and LOAD (fixed matmul loop run by
scripts/tools/matmul_load.py in a separate interpreter that has torch),
alternated per repeat in the configured order (I L I L I L by default).

Output is CSV only, never the store:
  x1_meta.csv     run settings, discovery results, git commit
  x1_raw.csv      one row per read (optional, --no-raw to skip)
  x1_summary.csv  one row per channel and step, plus pooled rows per condition

Definitions:
  arrival    a new sample: a new source timestamp (DCGM) or a successful
             read (hwmon carries no source timestamp, so read time is used)
  change     an arrival whose value differs from the previous arrival
  held       hwmon read equal to the previous read (source may not have updated)
  repeated   DCGM sample with a new timestamp and the same value
  missing    failed read or blank DCGM value (never stored as 0)
  duplicate  arrival whose timestamp equals the previous arrival's

All durations, intervals and limits come from config/app_settings.yaml,
section x1_update_rates. Attended runs only.

Usage:
  python scripts/x1_update_rates.py --probe
  python scripts/x1_update_rates.py --out-dir <dir> [--step-s 10 --repeats 1]
"""
import argparse
import csv
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # the script lives in scripts/; core is importable from the repo root
    sys.path.insert(0, str(ROOT))

from core.readers.spbm_energy_reader import (  # noqa: E402
    SPBMEnergyReader,
    SPBM_ENERGY_CHANNELS,
    SPBM_POWER_CHANNELS,
    SPBM_POWER_TELEMETRY_CHANNELS,
)

logger = logging.getLogger("x1_update_rates")

# One raw read: (channel, read time monotonic ns, source time ns or None, value or None)
Row = Tuple[str, int, Optional[int], Optional[int]]

SUMMARY_FIELDS = [
    "scope", "step_index", "repeat", "condition", "channel", "kind", "unit",
    "ts_basis", "n_reads", "n_missing", "n_missing_ts", "n_duplicate_ts",
    "n_arrivals", "arrival_p10_ms", "arrival_p50_ms", "arrival_p90_ms",
    "n_gaps_over_threshold", "n_changes", "change_p10_ms", "change_p50_ms",
    "change_p90_ms", "step_p10", "step_p50", "step_p90", "held", "repeated",
    "abort_reason", "note",
]
RAW_FIELDS = ["step_index", "repeat", "condition", "channel",
              "t_read_ns", "t_source_ns", "value"]


# --------------------------------------------------------------------------
# Configuration and channel discovery
# --------------------------------------------------------------------------

def load_config(path):
    # type: (Path) -> Dict
    """Load the x1_update_rates section; exit with a message when it is missing."""
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    cfg = data.get("x1_update_rates")
    if not cfg:
        raise SystemExit("x1_update_rates section missing in %s" % path)
    return cfg


def rail_labels():
    # type: () -> List[str]
    """The 10 rail labels: the reader's 4 power channels plus its 6 telemetry rails."""
    return list(SPBM_POWER_CHANNELS) + list(SPBM_POWER_TELEMETRY_CHANNELS)


def expected_spbm_channels():
    # type: () -> List[str]
    """X1 channel names expected from SPBM (4 counters, 10 rails)."""
    return (["counter:" + lab for lab in SPBM_ENERGY_CHANNELS]
            + ["rail:" + lab for lab in rail_labels()])


def discover_spbm():
    # type: () -> Tuple[Dict[str, Tuple[str, str]], List[str], Optional[str]]
    """
    Map X1 channels to sysfs paths through the SPBM reader's own discovery
    (device by hwmon name, channels by label), so the script and the
    platform reader never disagree on which file is which channel.
    hwmon numbering changes across reboots, so no index is used anywhere.

    Returns:
        (channels, missing, hwmon_path): channels maps 'counter:<label>' or
        'rail:<label>' to (unit, path); missing lists expected channels not found.
    """
    reader = SPBMEnergyReader({})
    hwmon = reader._hwmon_path
    if not hwmon:
        return {}, expected_spbm_channels(), None
    energy = reader._discover_energy_paths()
    power = reader._discover_power_paths()
    chans = {"counter:" + lab: ("uJ", energy[lab])
             for lab in SPBM_ENERGY_CHANNELS if lab in energy}
    chans.update({"rail:" + lab: ("uW", power[lab])
                  for lab in rail_labels() if lab in power})
    missing = [c for c in expected_spbm_channels() if c not in chans]
    return chans, missing, hwmon


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------

def read_sysfs(channel, path):
    # type: (str, str) -> Row
    """Read one hwmon file; a failed read is a missing value (None), never 0."""
    t_ns = time.monotonic_ns()
    try:
        value = int(Path(path).read_text().strip())
    except (OSError, ValueError) as exc:
        # counted as missing in the summary, so not silent
        logger.debug("read failed %s: %s", path, exc)
        value = None
    return (channel, t_ns, None, value)


def poll_hwmon(chans, stop, period_s, sink):
    # type: (Dict[str, Tuple[str, str]], threading.Event, float, List[Row]) -> None
    """Read every SPBM channel once per period until stop is set."""
    next_t = time.monotonic()
    while not stop.is_set():
        sink.extend([read_sysfs(name, meta[1]) for name, meta in chans.items()])
        next_t += period_s
        # sleep to the next slot; a late poll starts the next one at once
        stop.wait(max(0.0, next_t - time.monotonic()))


def open_dcgm(cfg):
    # type: (Dict) -> Tuple[Optional[object], Optional[str]]
    """
    Open a DCGM watch on the configured field through the DCGM Python
    bindings (installed with DCGM, not in the venv; paths from config).

    Returns:
        (reader, None) on success, (None, reason) otherwise.
    """
    dc = cfg["dcgm"]
    paths = [p for p in dc["bindings_paths"] if Path(p).is_dir()]
    if not paths:
        return None, "DCGM bindings not found"
    sys.path.insert(0, paths[0])
    try:
        import DcgmReader  # type: ignore[import]
        reader = DcgmReader.DcgmReader(
            fieldIds=[int(dc["field_id"])],
            updateFrequency=int(dc["update_ms"] * 1000),  # microseconds
            maxKeepAge=float(dc["max_keep_age_s"]),
        )
        # first call connects to the host engine and starts the watch
        reader.GetAllGpuValuesAsFieldIdDictSinceLastCall()
    except Exception as exc:  # bindings raise their own error types
        return None, "DCGM open failed: %s" % exc
    return reader, None


def fetch_dcgm(reader, field_id):
    # type: (object, int) -> List[object]
    """Return all DCGM values recorded since the last call, all GPUs flattened."""
    try:
        by_gpu = reader.GetAllGpuValuesAsFieldIdDictSinceLastCall()
    except Exception as exc:
        logger.warning("DCGM fetch failed: %s", exc)
        return []
    out = []  # type: List[object]
    for per_field in by_gpu.values():
        out.extend(per_field.get(field_id, []))
    return out


def dcgm_row(channel, t_read_ns, val):
    # type: (str, int, object) -> Row
    """Convert one DCGM value; DCGM stamps in microseconds, blanks are missing."""
    ts = getattr(val, "ts", None)
    ts_ns = int(ts) * 1000 if ts is not None else None
    if getattr(val, "isBlank", False) or getattr(val, "value", None) is None:
        return (channel, t_read_ns, ts_ns, None)
    return (channel, t_read_ns, ts_ns, int(val.value))


def poll_dcgm(reader, cfg, stop, sink):
    # type: (object, Dict, threading.Event, List[Row]) -> None
    """Collect DCGM values every poll period; one last fetch after stop."""
    fid = int(cfg["dcgm"]["field_id"])
    channel = "dcgm:%d" % fid
    poll_s = cfg["dcgm"]["poll_ms"] / 1000.0
    while not stop.is_set():
        t_ns = time.monotonic_ns()
        sink.extend([dcgm_row(channel, t_ns, v) for v in fetch_dcgm(reader, fid)])
        stop.wait(poll_s)
    t_ns = time.monotonic_ns()
    sink.extend([dcgm_row(channel, t_ns, v) for v in fetch_dcgm(reader, fid)])


def start_samplers(cfg, chans, dcgm):
    # type: (Dict, Dict, Optional[object]) -> Tuple[threading.Event, List[List[Row]], List[threading.Thread]]
    """Start the hwmon poller and, if DCGM is open, the DCGM poller."""
    stop = threading.Event()
    hw_sink, dc_sink = [], []  # type: List[Row], List[Row]
    threads = [threading.Thread(
        target=poll_hwmon, daemon=True,
        args=(chans, stop, cfg["hwmon_poll_ms"] / 1000.0, hw_sink))]
    if dcgm is not None:
        threads.append(threading.Thread(
            target=poll_dcgm, daemon=True, args=(dcgm, cfg, stop, dc_sink)))
    for th in threads:
        th.start()
    return stop, [hw_sink, dc_sink], threads


# --------------------------------------------------------------------------
# Safety: serving processes, GPU temperature, load process
# --------------------------------------------------------------------------

def gpu_temp_c():
    # type: () -> Optional[float]
    """GPU temperature from nvidia-smi; None when it cannot be read."""
    cmd = ["nvidia-smi", "--query-gpu=temperature.gpu",
           "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=10).stdout
        return float(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        logger.warning("GPU temperature read failed: %s", exc)
        return None


def server_processes(patterns):
    # type: (List[str]) -> List[str]
    """Running processes whose command line matches a serving pattern."""
    found = []  # type: List[str]
    for pat in patterns:
        res = subprocess.run(["pgrep", "-af", pat], capture_output=True, text=True)
        found.extend(res.stdout.splitlines())
    return found


def precheck_step(cfg, cond):
    # type: (Dict, str) -> Optional[str]
    """Reason to refuse the step, or None. Both conditions need no server."""
    procs = server_processes(cfg["server_process_patterns"])
    if procs:
        return "serving process running: %s" % procs[0][:120]
    temp = gpu_temp_c()
    if temp is None:
        return "gpu temperature unreadable"
    limit = cfg["thermal"]["max_start_temp_c"]
    if cond == "LOAD" and temp > limit:
        return "gpu %.0f C above LOAD start limit %.0f C" % (temp, limit)
    return None


def start_load(cfg, duration_s):
    # type: (Dict, float) -> subprocess.Popen
    """Start the fixed matmul load in the configured interpreter (torch lives there)."""
    load = cfg["load"]
    cmd = [load["python"], str(ROOT / "scripts" / "tools" / "matmul_load.py"),
           "--n", str(load["matrix_n"]), "--dtype", str(load["dtype"]),
           "--duty", str(load["duty"]), "--period-ms", str(load["period_ms"]),
           "--duration-s", str(duration_s), "--seed", str(load["seed"])]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def stop_load(proc):
    # type: (Optional[subprocess.Popen]) -> Optional[str]
    """Stop the load process and return its last output line (summary)."""
    if proc is None:
        return None
    if proc.poll() is None:
        proc.terminate()  # matmul_load prints its summary on SIGTERM
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    lines = (out or "").strip().splitlines()
    return lines[-1] if lines else None


def step_health(thermal, proc):
    # type: (Dict, Optional[subprocess.Popen]) -> Optional[str]
    """Abort reason during a step: temperature limit, unreadable under load, load exit."""
    temp = gpu_temp_c()
    if temp is None and proc is not None:
        return "gpu temperature unreadable during LOAD"
    if temp is not None and temp >= thermal["abort_temp_c"]:
        return "gpu %.0f C at or above abort limit %.0f C" % (temp, thermal["abort_temp_c"])
    if proc is not None and proc.poll() is not None:
        return "load process exited early (code %s)" % proc.returncode
    return None


def watch_step(cfg, proc, end_t):
    # type: (Dict, Optional[subprocess.Popen], float) -> Optional[str]
    """Wait until end_t, checking health each interval; return an abort reason or None."""
    thermal = cfg["thermal"]
    while time.monotonic() < end_t:
        time.sleep(min(thermal["check_interval_s"], max(0.0, end_t - time.monotonic())))
        reason = step_health(thermal, proc)
        if reason:
            return reason
    return None


def run_step(cfg, cond, chans, dcgm):
    # type: (Dict, str, Dict, Optional[object]) -> Tuple[List[Row], Optional[str], Optional[str]]
    """Run one IDLE or LOAD step; return raw rows, abort reason, load summary."""
    reason = precheck_step(cfg, cond)
    if reason:
        return [], reason, None
    proc = None
    if cond == "LOAD":
        warm = cfg["load"]["warmup_s"]
        # the load outlives the sampling window; it is stopped explicitly below
        proc = start_load(cfg, cfg["step_s"] + warm + 10)
        time.sleep(warm)  # let clocks and power settle before sampling
    if dcgm is not None:
        fetch_dcgm(dcgm, int(cfg["dcgm"]["field_id"]))  # drop values from before the step
    stop, sinks, threads = start_samplers(cfg, chans, dcgm)
    reason = watch_step(cfg, proc, time.monotonic() + cfg["step_s"])
    stop.set()
    for th in threads:
        th.join()
    load_line = stop_load(proc)
    return [r for s in sinks for r in s], reason, load_line


# --------------------------------------------------------------------------
# Statistics (pure functions, unit tested)
# --------------------------------------------------------------------------

def pct(xs, q):
    # type: (List[float], float) -> Optional[float]
    """Linear interpolation percentile, q in 0..100; None for an empty list."""
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def series_stats(rows, use_src):
    # type: (List[Row], bool) -> Dict
    """
    X1 statistics for one channel in one step.

    Args:
        rows: raw reads of one channel in read order.
        use_src: True when the source stamps its samples (DCGM): arrival time
            is the source time. False for hwmon: arrival time is read time.

    Returns:
        counts (n_reads, n_missing, n_missing_ts, n_dup_ts, n_arrivals, same)
        and lists (arrival_ms, change_ms, steps).
    """
    valid = [((r[2] if use_src else r[1]), r[3]) for r in rows if r[3] is not None]
    pts = [p for p in valid if p[0] is not None]
    pairs = list(zip(pts, pts[1:]))
    change_t = [b[0] for a, b in pairs if b[1] != a[1]]
    return {
        "n_reads": len(rows),
        "n_missing": len(rows) - len(valid),
        "n_missing_ts": len(valid) - len(pts),
        "n_arrivals": len(pts),
        "n_dup_ts": sum(1 for a, b in pairs if b[0] == a[0]),
        "same": sum(1 for a, b in pairs if b[1] == a[1]),
        "arrival_ms": [(b[0] - a[0]) / 1e6 for a, b in pairs],
        # the first change has no earlier change, so intervals start at the second
        "change_ms": [(t1 - t0) / 1e6 for t0, t1 in zip(change_t, change_t[1:])],
        "steps": [b[1] - a[1] for a, b in pairs if b[1] != a[1]],
    }


def merge_stats(parts):
    # type: (List[Dict]) -> Dict
    """Pool several steps of one channel: counts add, interval lists join."""
    out = {}  # type: Dict
    for st in parts:
        for key, val in st.items():
            out[key] = out.get(key, [] if isinstance(val, list) else 0) + val
    return out


def r3(x):
    # type: (Optional[float]) -> Optional[float]
    """Round to 3 decimals, keeping None (written as an empty CSV cell)."""
    return None if x is None else round(x, 3)


def summary_row(meta, st, use_src, gap_ms):
    # type: (Dict, Dict, bool, float) -> Dict
    """One summary row; held applies to hwmon, repeated to DCGM, the other is NULL."""
    arr, chg, steps = st["arrival_ms"], st["change_ms"], st["steps"]
    row = dict(meta)
    row.update({
        "ts_basis": "source" if use_src else "read",
        "n_reads": st["n_reads"], "n_missing": st["n_missing"],
        "n_missing_ts": st["n_missing_ts"], "n_duplicate_ts": st["n_dup_ts"],
        "n_arrivals": st["n_arrivals"],
        "arrival_p10_ms": r3(pct(arr, 10)), "arrival_p50_ms": r3(pct(arr, 50)),
        "arrival_p90_ms": r3(pct(arr, 90)),
        "n_gaps_over_threshold": sum(1 for x in arr if x > gap_ms),
        "n_changes": len(steps),
        "change_p10_ms": r3(pct(chg, 10)), "change_p50_ms": r3(pct(chg, 50)),
        "change_p90_ms": r3(pct(chg, 90)),
        "step_p10": r3(pct(steps, 10)), "step_p50": r3(pct(steps, 50)),
        "step_p90": r3(pct(steps, 90)),
        "held": None if use_src else st["same"],
        "repeated": st["same"] if use_src else None,
    })
    return row


# --------------------------------------------------------------------------
# Plan, CSV output, report
# --------------------------------------------------------------------------

def channel_meta(channel, chans):
    # type: (str, Dict) -> Dict
    """Kind and unit of a channel name ('counter:pkg', 'rail:gpu', 'dcgm:156')."""
    kind = channel.split(":")[0]
    unit = chans[channel][0] if channel in chans else ("mJ" if kind == "dcgm" else None)
    return {"channel": channel, "kind": kind, "unit": unit}


def step_stats(rows):
    # type: (List[Row]) -> Dict[str, Dict]
    """Group raw rows by channel and compute series_stats for each."""
    by_ch = {}  # type: Dict[str, List[Row]]
    for r in rows:
        by_ch.setdefault(r[0], []).append(r)
    return {ch: series_stats(rs, ch.startswith("dcgm:")) for ch, rs in by_ch.items()}


def append_raw(path, idx, rep, cond, rows):
    # type: (Path, int, int, str, List[Row]) -> None
    """Append one step's raw reads; None is written as an empty cell (NULL)."""
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(RAW_FIELDS)
        w.writerows([(idx, rep, cond, r[0], r[1], r[2], r[3]) for r in rows])


def run_plan(cfg, chans, dcgm, out_dir, write_raw):
    # type: (Dict, Dict, Optional[object], Path, bool) -> List[Dict]
    """Run the step plan (order x repeats); stop the plan at the first abort."""
    plan = [(rep, cond) for rep in range(1, int(cfg["repeats"]) + 1)
            for cond in cfg["order"]]
    steps = []  # type: List[Dict]
    for idx, (rep, cond) in enumerate(plan):
        logger.info("step %d/%d %s repeat %d", idx + 1, len(plan), cond, rep)
        rows, reason, load_line = run_step(cfg, cond, chans, dcgm)
        steps.append({"step_index": idx, "repeat": rep, "condition": cond,
                      "stats": step_stats(rows), "abort_reason": reason,
                      "load": load_line})
        if write_raw:
            append_raw(out_dir / "x1_raw.csv", idx, rep, cond, rows)
        if reason:
            break  # attended fail safe: no further steps after an abort
        if idx < len(plan) - 1:
            time.sleep(cfg["settle_s"])
    return steps


def dcgm_note(cfg, st):
    # type: (Dict, Optional[Dict]) -> Optional[str]
    """Note when DCGM arrivals are too sparse to resolve cadence below them."""
    limit = cfg["dcgm"]["resolution_note_ms"]
    p50 = pct(st["arrival_ms"], 50) if st else None
    if p50 is not None and p50 >= limit:
        return "cadence not resolvable below the %.0f ms sampling interval" % p50
    return None


def channel_note(cfg, channel, st):
    # type: (Dict, str, Dict) -> Optional[str]
    """
    Resolution note for a summary row. A hwmon channel whose value changes
    at about every read updates at least as fast as the poll, so its true
    cadence is below what this run can resolve.
    """
    if channel.startswith("dcgm:"):
        return dcgm_note(cfg, st)
    if not st["steps"]:
        return "no value change in step"
    p50 = pct(st["change_ms"], 50)
    limit = cfg["hwmon_poll_ms"] * cfg["hwmon_unresolved_factor"]
    if p50 is not None and p50 <= limit:
        return "cadence not resolvable below the %s ms poll interval" % cfg["hwmon_poll_ms"]
    return None


def channel_rows(cfg, chans, scope, meta, stats):
    # type: (Dict, Dict, str, Dict, Dict[str, Dict]) -> List[Dict]
    """Summary rows for every expected channel; a channel without data gets a note."""
    rows = []
    for ch in expected_channels(cfg):
        base = dict(meta, scope=scope, **channel_meta(ch, chans))
        st = stats.get(ch)
        if not st:
            rows.append(dict(base, note="no data"))
            continue
        row = summary_row(base, st, ch.startswith("dcgm:"), cfg["gap_threshold_ms"])
        row["note"] = channel_note(cfg, ch, st)
        rows.append(row)
    return rows


def expected_channels(cfg):
    # type: (Dict) -> List[str]
    """All 15 X1 channels: 4 counters, 10 rails, DCGM field."""
    return expected_spbm_channels() + ["dcgm:%d" % int(cfg["dcgm"]["field_id"])]


def pooled_stats(steps, cond):
    # type: (List[Dict], str) -> Dict[str, Dict]
    """Pool every completed step of one condition, per channel."""
    done = [s for s in steps if s["condition"] == cond and not s["abort_reason"]]
    chans = {ch for s in done for ch in s["stats"]}
    return {ch: merge_stats([s["stats"][ch] for s in done if ch in s["stats"]])
            for ch in chans}


def write_summary(cfg, chans, steps, path):
    # type: (Dict, Dict, List[Dict], Path) -> List[Dict]
    """Write per step rows, then pooled rows per condition; return all rows."""
    rows = []  # type: List[Dict]
    for s in steps:
        meta = {"step_index": s["step_index"], "repeat": s["repeat"],
                "condition": s["condition"], "abort_reason": s["abort_reason"]}
        rows.extend(channel_rows(cfg, chans, "step", meta, s["stats"]))
    for cond in cfg["order"]:
        meta = {"repeat": "all", "condition": cond}
        rows.extend(channel_rows(cfg, chans, "pooled", meta, pooled_stats(steps, cond)))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def git_commit():
    # type: () -> Optional[str]
    """Commit of the code that produced the CSV (None if git is unavailable)."""
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or None
    except OSError as exc:
        logger.warning("git commit lookup failed: %s", exc)
        return None


def write_meta(cfg, path, hwmon, missing, dcgm_reason, steps):
    # type: (Dict, Path, Optional[str], List[str], Optional[str], List[Dict]) -> None
    """Write run settings and discovery results as key,value rows."""
    items = [("utc_end", datetime.now(timezone.utc).isoformat()),
             ("git_commit", git_commit()), ("spbm_hwmon", hwmon),
             ("spbm_missing_channels", ";".join(missing) or None),
             ("dcgm_status", dcgm_reason or "open")]
    items += [("cfg." + k, v) for k, v in cfg.items() if not isinstance(v, dict)]
    items += [("cfg.%s.%s" % (k, kk), vv) for k, v in cfg.items()
              if isinstance(v, dict) for kk, vv in v.items()]
    items += [("load_step_%d" % s["step_index"], s["load"]) for s in steps if s["load"]]
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows([("key", "value")] + items)


def report(cfg, rows, steps):
    # type: (Dict, List[Dict], List[Dict]) -> int
    """Print the package counter check and completeness; return the exit code."""
    chk = cfg["check"]
    ok = not any(s["abort_reason"] for s in steps)
    pkg = [r for r in rows if r["scope"] == "pooled" and r["channel"] == "counter:pkg"]
    for r in pkg:
        p50 = r.get("change_p50_ms")
        good = p50 is not None and chk["pkg_change_p50_ms_low"] <= p50 <= chk["pkg_change_p50_ms_high"]
        ok = ok and good
        print("pkg counter %s change p50 %s ms: %s" % (r["condition"], p50, "PASS" if good else "FAIL"))
    gaps = [r for r in rows if r["scope"] == "step" and (r.get("note") == "no data" or r.get("n_missing"))]
    print("incomplete channel steps: %d" % len(gaps))
    for r in gaps[:20]:
        print("  step %s %s %s missing=%s" % (r["step_index"], r["condition"], r["channel"], r.get("n_missing")))
    for s in steps:
        if s["abort_reason"]:
            print("ABORTED step %d: %s" % (s["step_index"], s["abort_reason"]))
    return 0 if ok else 1


def probe(cfg, chans, missing, hwmon, dcgm, dcgm_reason):
    # type: (Dict, Dict, List[str], Optional[str], Optional[object], Optional[str]) -> int
    """Read every channel a few times and DCGM for 1 s; print only, no files."""
    print("spbm hwmon: %s; missing: %s" % (hwmon, missing or "none"))
    for name, (unit, path) in sorted(chans.items()):
        vals = [read_sysfs(name, path)[3] for _ in range(3)]
        print("  %-16s %-3s %s %s" % (name, unit, path, vals))
    print("dcgm: %s" % (dcgm_reason or "open"))
    if dcgm is not None:
        time.sleep(1.0)
        vals = [dcgm_row("dcgm", 0, v) for v in fetch_dcgm(dcgm, int(cfg["dcgm"]["field_id"]))]
        print("  dcgm values in 1 s: %d; last: %s" % (len(vals), vals[-1:] or None))
    print("gpu temp C: %s" % gpu_temp_c())
    print("serving processes: %s" % (server_processes(cfg["server_process_patterns"]) or "none"))
    return 0 if not missing and dcgm is not None else 1


def parse_args(argv=None):
    # type: (Optional[List[str]]) -> argparse.Namespace
    """Command line; overrides are for short smoke runs only."""
    ap = argparse.ArgumentParser(description="X1 update rate measurement (CSV only).")
    ap.add_argument("--config", default=str(ROOT / "config" / "app_settings.yaml"))
    ap.add_argument("--out-dir", help="parent folder; a timestamped subfolder is created")
    ap.add_argument("--probe", action="store_true", help="discover and read once, no files")
    ap.add_argument("--step-s", type=float, help="override step_s")
    ap.add_argument("--repeats", type=int, help="override repeats")
    ap.add_argument("--settle-s", type=float, help="override settle_s")
    ap.add_argument("--poll-ms", type=float, help="override hwmon_poll_ms")
    ap.add_argument("--conditions", help="override order, e.g. IDLE or IDLE,LOAD")
    ap.add_argument("--no-raw", action="store_true", help="skip x1_raw.csv")
    return ap.parse_args(argv)


def apply_overrides(cfg, args):
    # type: (Dict, argparse.Namespace) -> Dict
    """Return a copy of cfg with the command line overrides applied."""
    cfg = dict(cfg)
    for key, val in (("step_s", args.step_s), ("repeats", args.repeats),
                     ("settle_s", args.settle_s), ("hwmon_poll_ms", args.poll_ms)):
        if val is not None:
            cfg[key] = val
    if args.conditions:
        cfg["order"] = [c.strip().upper() for c in args.conditions.split(",")]
    bad = [c for c in cfg["order"] if c not in ("IDLE", "LOAD")]
    if bad:
        raise SystemExit("unknown condition(s): %s" % bad)
    return cfg


def main(argv=None):
    # type: (Optional[List[str]]) -> int
    """Entry point: probe, or run the plan and write the CSVs."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = apply_overrides(load_config(Path(args.config)), args)
    chans, missing, hwmon = discover_spbm()
    dcgm, dcgm_reason = open_dcgm(cfg)
    if args.probe:
        return probe(cfg, chans, missing, hwmon, dcgm, dcgm_reason)
    if not args.out_dir:
        raise SystemExit("--out-dir is required unless --probe")
    out = Path(args.out_dir) / ("x1_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=False)  # never overwrite an earlier run
    steps = run_plan(cfg, chans, dcgm, out, not args.no_raw)
    rows = write_summary(cfg, chans, steps, out / "x1_summary.csv")
    write_meta(cfg, out / "x1_meta.csv", hwmon, missing, dcgm_reason, steps)
    print("CSV written to %s" % out)
    return report(cfg, rows, steps)


if __name__ == "__main__":
    sys.exit(main())
