"""
alems_plugin_pravaha.kernel_pass: per kernel L2 read and write bytes from a separate
Nsight Compute pass, with an explicit comparison eligibility against a matched run.

Author: Deepak Panigrahy

What is measured (method kernel_traffic_ncu_v1). Per kernel launch, from the GPU's
L2 cache, through pravaha_mem.kernels (metric names and constants are pravaha's):
  read_sectors         lts__d_sectors_fill_sysmem.sum               sectors filled from memory (L2 misses)
  write_sectors        lts__t_sectors_aperture_sysmem_op_write.sum  sectors written toward memory
  write_requests       lts__t_requests_aperture_sysmem_op_write.sum diagnostic only (one request may touch several sectors)
  device_fill_sectors  lts__d_sectors_fill_device.sum               diagnostic only (expected 0 on GB10)
  read_bytes  = read_sectors  x 32      write_bytes = write_sectors x 32
These are per kernel L2 read and write bytes. They are not the logical copy bytes
of 45.3b (copy engine traffic never appears here).

A profiling pass is never timed and never used for energy: Nsight Compute replays
each kernel and, with cache control "all", flushes caches before each replay
iteration. The pass runs outside the measured path; the live pravaha reader is not
started for it. The matched normal run supplies the reference.

Comparison eligibility (agreement_status), evaluated in this order:
  no_reference           no matched run, or the matched run has no traffic record
  insufficient_coverage  matched traffic record not 'measured' or without samples
  scope_mismatch         kernel regex, launch limit or a declared subset (scope_subset, for
                         example a short characterisation run) does not cover the whole run
  device_mismatch        matched reader device differs from the pass device
  zero_reference         matched kernel bytes measured as 0 (kept; ratio NULL)
  within / outside       ratios computed; tolerance is a diagnostic band, not an uncertainty
Reads and writes are compared separately: pass read_bytes against the run's
kernel_read_miss_bytes, pass write_bytes against kernel_write_bytes.
The reference is device wide and covers setup and background kernels as well
(documented; a pass covering the whole program keeps the same scope).

A pass never completes with apparent zero traffic: timeout, nonzero exit, no
kernel data or a missing counter gives status 'failed' with a reason, and NULL
aggregates.

Configuration (~/.alemsrc or shell; named defaults only):
  NCU                             Nsight Compute binary (else PATH, else CUDA default; pravaha.find_ncu)
  ALEMS_KTRAFFIC_CACHE_CONTROL    all | none                  (default all)
  ALEMS_KTRAFFIC_REPLAY_MODE      ncu replay mode             (default kernel)
  ALEMS_KTRAFFIC_NAME_BASE        name the regex matches: function | demangled | mangled (default demangled)
  ALEMS_KTRAFFIC_LAUNCH_COUNT     profiled launch limit       (default none)
  ALEMS_KTRAFFIC_TOL              diagnostic band             (default 0.25)
  ALEMS_KTRAFFIC_TIMEOUT_S        pass timeout                (default 3600)
  ALEMS_KTRAFFIC_PROBE_CMD        override of the probe (default: pravaha_mem.kernels.probe); recorded
  ALEMS_GPU_INDEX                 device of the pass          (default 0)
"""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

METHOD_ID = "kernel_traffic_ncu_v1"
SECTOR_BYTES = 32
DEFAULTS = {"cache_control": "all", "replay_mode": "kernel", "name_base": "demangled", "launch_count": None,
            "tol": 0.25, "timeout_s": 3600.0}


def _env(name, default):
    # type: (str, Any) -> Any
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def settings(overrides=None):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Effective profiling settings: named default, then environment, then step or CLI values."""
    s = {"cache_control": _env("ALEMS_KTRAFFIC_CACHE_CONTROL", DEFAULTS["cache_control"]),
         "replay_mode": _env("ALEMS_KTRAFFIC_REPLAY_MODE", DEFAULTS["replay_mode"]),
         "name_base": _env("ALEMS_KTRAFFIC_NAME_BASE", DEFAULTS["name_base"]),
         "launch_count": _env("ALEMS_KTRAFFIC_LAUNCH_COUNT", DEFAULTS["launch_count"]),
         "tol": float(_env("ALEMS_KTRAFFIC_TOL", DEFAULTS["tol"])),
         "timeout_s": float(_env("ALEMS_KTRAFFIC_TIMEOUT_S", DEFAULTS["timeout_s"])),
         "match": None}
    for k, v in (overrides or {}).items():
        if v is not None:
            s[k] = v
    if s["launch_count"] is not None:
        s["launch_count"] = int(s["launch_count"])
    return s


def _metrics():
    from pravaha_mem import kernels as pk
    return pk, [pk.READ, pk.WRITE, pk.WRITE_REQ, pk.DEVICE_FILL]


def ncu_info():
    # type: () -> Dict[str, Optional[str]]
    """Resolved ncu path and version, or the reason it is missing; never raises."""
    try:
        pk, _ = _metrics()
        path = pk.find_ncu()
    except Exception as exc:
        return {"path": None, "version": None, "reason": str(exc)}
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30).stdout
        ver = (out.strip().splitlines() or [""])[-1]
    except Exception as exc:
        return {"path": path, "version": None, "reason": "version: %s" % exc}
    return {"path": path, "version": ver, "reason": None}


def _driver_flag():
    # type: () -> Optional[str]
    """Driver profiling restriction, as diagnostic evidence only (None when not exposed)."""
    try:
        with open("/proc/driver/nvidia/params") as f:
            for line in f:
                if line.lower().startswith("rmprofilingadminonly"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def run_ncu(command, s, env=None):
    # type: (List[str], Dict[str, Any], Optional[Dict[str, str]]) -> Dict[str, Any]
    """
    One ncu pass. Returns status 'completed' with kernel rows, or 'failed' with a
    reason (timeout, workload_failed, no_kernel_data, counter_unavailable, ncu_missing).
    """
    pk, metrics = _metrics()
    try:
        ncu = pk.find_ncu()
    except Exception as exc:
        return {"status": "failed", "reason": "ncu_missing", "detail": str(exc)}
    args = [ncu, "--csv", "--print-units", "base", "--replay-mode", s["replay_mode"],
            "--cache-control", s["cache_control"], "--metrics", ",".join(metrics)]
    if s.get("match"):
        args += ["--kernel-name-base", s["name_base"], "--kernel-name", "regex:%s" % s["match"]]
    if s.get("launch_count"):
        args += ["-c", str(s["launch_count"])]
    args += list(command)
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=s["timeout_s"], env=env)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "reason": "timeout", "detail": "ncu exceeded %.0f s" % s["timeout_s"]}
    order, vals = pk.parse(p.stdout.splitlines())
    tail = (p.stderr or "").strip()[-300:]
    if p.returncode != 0:
        return {"status": "failed", "reason": "workload_failed", "detail": "exit %s: %s" % (p.returncode, tail)}
    if not order:
        if "ERR_NVGPUCTRPERM" in p.stdout + p.stderr:
            return {"status": "failed", "reason": "no_permission", "detail": tail}
        if s.get("match"):
            return {"status": "failed", "reason": "no_kernel_matched",
                    "detail": "no kernel name (%s) matched regex %r" % (s["name_base"], s["match"])}
        return {"status": "failed", "reason": "no_kernel_data", "detail": tail}
    rows = []
    for seq, key in enumerate(order):
        d = vals[key]
        missing = [m for m in metrics[:2] if m not in d]   # read and write are required
        if missing:
            return {"status": "failed", "reason": "counter_unavailable",
                    "detail": "kernel %s: %s" % (key[1], ",".join(missing))}
        rows.append({"seq": seq, "ncu_id": key[0], "kernel_name": key[1],
                     "read_sectors": int(d[metrics[0]]), "write_sectors": int(d[metrics[1]]),
                     "write_requests": _int_or_none(d.get(metrics[2])),
                     "device_fill_sectors": _int_or_none(d.get(metrics[3]))})
    for r in rows:
        r["read_bytes"] = r["read_sectors"] * SECTOR_BYTES
        r["write_bytes"] = r["write_sectors"] * SECTOR_BYTES
    return {"status": "completed", "reason": None, "detail": None, "kernels": rows,
            "read_bytes": sum(r["read_bytes"] for r in rows),
            "write_bytes": sum(r["write_bytes"] for r in rows)}


def _int_or_none(v):
    return None if v is None else int(v)


def probe():
    # type: () -> Dict[str, Any]
    """
    Feasibility: ncu resolves, and the full metric set returns numbers for a tiny kernel.
    Default: pravaha_mem.kernels.probe() (pravaha >= 1.1.0). ALEMS_KTRAFFIC_PROBE_CMD
    overrides it; its use is recorded in probe_source. Result pass, fail or unavailable.
    """
    info = ncu_info()
    ev = {"ncu_path": info["path"], "ncu_version": info["version"], "driver_profiling_flag": _driver_flag()}
    if info["path"] is None:
        return dict(ev, feasibility="unavailable", reason=info["reason"], probe_source=None)
    cmd = _env("ALEMS_KTRAFFIC_PROBE_CMD", None)
    if not cmd:
        try:
            from pravaha_mem.kernels import probe as pravaha_probe
        except ImportError:
            return dict(ev, feasibility="unavailable", probe_source=None,
                        reason="pravaha-mem >= 1.1.0 (kernels.probe) or ALEMS_KTRAFFIC_PROBE_CMD needed")
        r = pravaha_probe()
        return dict(ev, feasibility="pass" if r["ok"] else "fail", reason=r["reason"], probe_source="pravaha")
    r = run_ncu(shlex.split(cmd), settings({"timeout_s": 300.0}))
    src = "env:ALEMS_KTRAFFIC_PROBE_CMD"
    if r["status"] != "completed":
        return dict(ev, feasibility="fail", reason="%s: %s" % (r["reason"], r["detail"]), probe_source=src)
    if r["write_bytes"] <= 0:
        return dict(ev, feasibility="fail", reason="probe kernel wrote 0 bytes", probe_source=src)
    return dict(ev, feasibility="pass", reason=None, probe_source=src)


def reference(conn, run_id):
    # type: (Any, Optional[int]) -> Optional[Dict[str, Any]]
    """Kernel read and write bytes of the matched run from its pravaha record, or None."""
    if run_id is None:
        return None
    row = conn.execute("SELECT status, device, sample_count FROM energy_validation_gpu_mem_traffic_runs "
                       "WHERE run_id=? ORDER BY rowid LIMIT 1", (run_id,)).fetchone()
    if row is None:
        return None
    sums = conn.execute("SELECT SUM(kernel_read_miss_bytes), SUM(kernel_write_bytes), COUNT(*) "
                        "FROM energy_validation_gpu_mem_traffic_samples WHERE run_id=?", (run_id,)).fetchone()
    return {"status": row[0], "device": row[1], "samples": sums[2] or 0,
            "read_bytes": sums[0], "write_bytes": sums[1]}


def agreement(passed, ref, s, device):
    # type: (Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any], str) -> Dict[str, Any]
    """agreement_status and the separate read and write ratios (NULL unless comparable)."""
    out = {"ref_read_bytes": None, "ref_write_bytes": None, "read_ratio": None, "write_ratio": None}
    if ref is None:
        return dict(out, agreement_status="no_reference")
    out.update(ref_read_bytes=ref["read_bytes"], ref_write_bytes=ref["write_bytes"])
    if ref["status"] != "measured" or not ref["samples"] or ref["read_bytes"] is None:
        return dict(out, agreement_status="insufficient_coverage")
    if s.get("match") or s.get("launch_count") or s.get("scope_subset"):
        return dict(out, agreement_status="scope_mismatch")
    if ref["device"] not in (None, "") and str(ref["device"]) != str(device):
        return dict(out, agreement_status="device_mismatch")
    if not ref["read_bytes"] or not ref["write_bytes"]:
        return dict(out, agreement_status="zero_reference")
    rr, wr = passed["read_bytes"] / ref["read_bytes"], passed["write_bytes"] / ref["write_bytes"]
    ok = abs(rr - 1) <= s["tol"] and abs(wr - 1) <= s["tol"]
    return dict(out, read_ratio=rr, write_ratio=wr, agreement_status="within" if ok else "outside")


def workload_identity(command, interpreter, phase):
    # type: (List[str], Optional[str], Optional[str]) -> Dict[str, Any]
    ident = {"command": list(command), "interpreter": interpreter, "phase": phase}
    ident["sha256"] = hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()
    return ident


_PASS_COLS = ("matched_run_id", "attempt_id", "config_sha256", "workload_json", "workload_sha256",
              "device", "ncu_path", "ncu_version", "driver_profiling_flag", "feasibility",
              "feasibility_reason", "metric_set", "cache_control", "replay_mode", "match_regex",
              "launch_count", "status", "reason", "reason_detail", "kernel_count", "read_bytes",
              "write_bytes", "ref_read_bytes", "ref_write_bytes", "read_ratio", "write_ratio",
              "tolerance", "agreement_status", "method_id", "started_utc_ns", "ended_utc_ns")
_KERNEL_COLS = ("seq", "ncu_id", "kernel_name", "read_sectors", "write_sectors", "write_requests",
                "device_fill_sectors", "read_bytes", "write_bytes")


def store_pass(conn, row, kernels):
    # type: (Any, Dict[str, Any], List[Dict[str, Any]]) -> int
    """Pass row and its kernel rows in one transaction; returns pass_id."""
    conn.execute("BEGIN")
    try:
        cur = conn.execute("INSERT INTO energy_validation_kernel_traffic_passes (%s) VALUES (%s)"
                           % (", ".join(_PASS_COLS), ", ".join("?" * len(_PASS_COLS))),
                           tuple(row.get(c) for c in _PASS_COLS))
        pid = cur.lastrowid
        conn.executemany("INSERT INTO energy_validation_kernel_traffic_kernels (pass_id, %s) VALUES (?, %s)"
                         % (", ".join(_KERNEL_COLS), ", ".join("?" * len(_KERNEL_COLS))),
                         [tuple([pid] + [k.get(c) for c in _KERNEL_COLS]) for k in kernels])
        conn.execute("COMMIT")
        return pid
    except Exception:
        conn.execute("ROLLBACK")
        raise


def profile(conn, command, overrides=None, matched_run_id=None, attempt_id=None, config_sha256=None,
            interpreter=None, phase=None, env=None):
    # type: (Any, List[str], Optional[Dict[str, Any]], Optional[int], Optional[int], Optional[str], Optional[str], Optional[str], Optional[Dict[str, str]]) -> Dict[str, Any]
    """Probe, run the pass, judge eligibility and store it. Returns the stored row plus pass_id."""
    s = settings(overrides)
    device = str(_env("ALEMS_GPU_INDEX", "0"))
    ident = workload_identity(command, interpreter, phase)
    feas = probe()
    t0 = time.time_ns()
    if feas["feasibility"] != "pass":
        r = {"status": "failed", "reason": "infeasible", "detail": feas["reason"]}
    else:
        r = run_ncu(command, s, env=env)
    t1 = time.time_ns()
    _, metrics = _metrics()
    row = {"matched_run_id": matched_run_id, "attempt_id": attempt_id, "config_sha256": config_sha256,
           "workload_json": json.dumps(ident, sort_keys=True), "workload_sha256": ident["sha256"],
           "device": device, "ncu_path": feas["ncu_path"], "ncu_version": feas["ncu_version"],
           "driver_profiling_flag": feas["driver_profiling_flag"], "feasibility": feas["feasibility"],
           "feasibility_reason": "%s (probe: %s)" % (feas["reason"], feas.get("probe_source")) if feas["reason"]
           else "probe: %s" % feas.get("probe_source"), "metric_set": ",".join(metrics),
           "cache_control": s["cache_control"], "replay_mode": s["replay_mode"], "match_regex": s.get("match"),
           "launch_count": s.get("launch_count"), "status": r["status"], "reason": r.get("reason"),
           "reason_detail": r.get("detail"), "tolerance": s["tol"], "method_id": METHOD_ID,
           "started_utc_ns": t0, "ended_utc_ns": t1}
    if r["status"] == "completed":
        row.update(kernel_count=len(r["kernels"]), read_bytes=r["read_bytes"], write_bytes=r["write_bytes"])
        row.update(agreement(r, reference(conn, matched_run_id), s, device))
    else:
        row["agreement_status"] = "not_evaluated"
    row["pass_id"] = store_pass(conn, row, r.get("kernels") or [])
    return row


class KernelTrafficStep(StepKindABC):
    """Profiling step: one Nsight Compute pass of a configured command (never measured for energy)."""

    KIND = "kernel_traffic"
    MEASUREMENT = "profiling"   # the sequencer runs measure() directly, outside the measured path
    PARAMS = {
        "command":        {"type": "string", "required": True},
        "match":          {"type": "string"},
        "launch_count":   {"type": "number"},
        "matched_run_id": {"type": "number"},
        "phase":          {"type": "string"},
    }
    ALEMS_PLUGIN_META = {"plugin_id": "pravaha_kernel_traffic", "family": "execution",
                         "extension_point": "alems.sequencer.steps", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        info = ncu_info()
        if info["path"] is None:
            return Availability(False, "ncu_missing", info["reason"] or "")
        return Availability(True)

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        cmd = shlex.split(step["command"])
        row = profile(ctx["db"].db.conn, cmd, {"match": step.get("match"), "launch_count": step.get("launch_count")},
                      matched_run_id=step.get("matched_run_id"), attempt_id=ctx.get("attempt_id"),
                      config_sha256=ctx.get("config_sha256"), interpreter=cmd[0], phase=step.get("phase"))
        if row["status"] != "completed":
            raise StepFailed(row["reason"] or "failed", row.get("reason_detail") or "")
        return {"kernel_pass_id": (row["pass_id"], None), "kernel_count": (row["kernel_count"], None),
                "l2_read_bytes": (row["read_bytes"], "B"), "l2_write_bytes": (row["write_bytes"], "B"),
                "agreement_status": (row["agreement_status"], None)}
