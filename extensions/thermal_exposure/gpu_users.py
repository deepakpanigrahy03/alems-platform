"""
Other GPU users and declared idle state (thermal_exposure extension).

Read only: lists GPU processes through NVML and never stops or signals any
process (shared machines, no sudo). NVML is optional: without it, or
without process visibility, every result is 'unknown', never 'pass'.

The recorder is paused during runs, so these checks only see checkpoints:
between runs, just before each run starts and just after it ends. A clean
result therefore means "no external activity seen at checkpoints", never
"the runs were isolated"; activity inside runs is not observable.

Evidence levels (separate, as reviewed):
  external activity  an external process with utilisation above zero  -> fail
  external process   an external process holding a GPU context only   -> warn
  none at checkpoints                                                  -> pass
  no visibility                                                        -> unknown
Memory use or total utilisation is never used to attribute activity.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


def load_nvml() -> Optional[Any]:
    """The pynvml module, initialised, or None (absence is not an error)."""
    try:
        import pynvml                    # optional dependency
        pynvml.nvmlInit()
        return pynvml
    except Exception as exc:             # no driver, no library, no GPU
        logger.info("NVML unavailable for GPU user checks: %s", exc)
        return None


def process_name(pid: int) -> Optional[str]:
    """Command name of a process from /proc (Linux), or None elsewhere."""
    try:
        with open("/proc/%d/comm" % pid) as fh:
            return fh.read().strip()
    except OSError:
        return None


def snapshot(nvml: Optional[Any], gpu_index: int = 0) -> Optional[List[Dict[str, Any]]]:
    """
    One checkpoint: GPU processes with memory and, where the driver gives
    it, per-process utilisation.

    Returns:
        List of {"pid", "name", "mem_bytes", "util"} (util None when not
        available), or None when the process list cannot be read.
    """
    if nvml is None:
        return None
    try:
        h = nvml.nvmlDeviceGetHandleByIndex(gpu_index)
        procs = nvml.nvmlDeviceGetComputeRunningProcesses(h)
    except Exception as exc:
        logger.info("GPU process list unavailable: %s", exc)
        return None
    utils = _process_utils(nvml, h)
    return [{"pid": int(p.pid), "name": process_name(int(p.pid)),
             "mem_bytes": getattr(p, "usedGpuMemory", None),
             "util": utils.get(int(p.pid)) if utils is not None else None}
            for p in procs]


def _process_utils(nvml: Any, handle: Any) -> Optional[Dict[int, int]]:
    """SM utilisation per pid over the driver's recent window, or None."""
    try:
        samples = nvml.nvmlDeviceGetProcessUtilization(handle, 0)
    except Exception:                    # not supported on every GPU or driver
        return None
    out: Dict[int, int] = {}
    for s in samples:
        out[int(s.pid)] = max(out.get(int(s.pid), 0), int(getattr(s, "smUtil", 0)))
    return out


def own_pids(server_names: Iterable[str] = (), server_pids: Iterable[int] = ()) -> Set[int]:
    """This process plus configured model-server processes (by pid or command name)."""
    pids = {os.getpid()} | {int(p) for p in server_pids}
    names = {n for n in server_names if n}
    if not names or not os.path.isdir("/proc"):
        return pids                       # no names, or no /proc (not Linux)
    numeric = (int(e) for e in os.listdir("/proc") if e.isdigit())
    return pids | {pid for pid in numeric if process_name(pid) in names}


def classify(snapshots: List[Optional[List[Dict[str, Any]]]], own: Set[int]) -> Dict[str, Any]:
    """
    Combine checkpoints into the three recording checks.

    Returns:
        {"activity": result, "process": result, "util_known": bool,
         "external": sorted external pids, "checkpoints": n}
    """
    seen = [s for s in snapshots if s is not None]
    if not seen:
        return {"activity": "unknown", "process": "unknown", "util_known": False,
                "external": [], "checkpoints": 0}
    ext = [p for s in seen for p in s if p["pid"] not in own]
    util_known = any(p["util"] is not None for s in seen for p in s)
    active = [p for p in ext if p["util"] is not None and p["util"] > 0]
    if active:
        activity = "fail"
    elif ext and not util_known:
        activity = "unknown"             # a context is visible but activity cannot be told
    else:
        activity = "pass"
    process = "warn" if ext else "pass"
    return {"activity": activity, "process": process, "util_known": util_known,
            "external": sorted({p["pid"] for p in ext}), "checkpoints": len(seen)}


def verify_idle_state(declared: Optional[str], snapshots: List[Optional[List[Dict[str, Any]]]],
                      server_names: Iterable[str]) -> Dict[str, Any]:
    """
    Evidence that the declared idle state held at the checkpoints.

    server_loaded: the configured server process must be visible with the
    same pid at every checkpoint (fail if absent or its pid changed).
    server_stopped: no configured server process may be visible.
    Without a declaration, server names or visibility: unknown.
    """
    names = {n for n in server_names if n}
    seen = [s for s in snapshots if s is not None]
    if declared is None or not names or not seen:
        return {"result": "unknown", "note": "no declaration, server name or visibility"}
    per_cp = [{p["pid"] for p in s if p["name"] in names} for s in seen]
    if declared == "server_stopped":
        bad = any(per_cp)
        return {"result": "fail" if bad else "pass",
                "note": "server process visible while declared stopped" if bad else "no server process"}
    if not all(per_cp):
        return {"result": "fail", "note": "declared loaded but server process absent at a checkpoint"}
    if len({frozenset(c) for c in per_cp}) > 1:   # a server may run several workers
        return {"result": "fail", "note": "server pid changed (restart or reload)"}
    return {"result": "pass", "note": "server pid %s at %d checkpoints" % (sorted(per_cp[0]), len(seen))}
