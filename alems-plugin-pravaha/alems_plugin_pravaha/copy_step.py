"""
alems_plugin_pravaha.copy_step: steady pinned CUDA copy at a known size, as a sequencer step.

Author: Deepak Panigrahy

Workload: pravaha_mem.known_bytes (pinned host buffer via cudaMallocHost, two
device buffers cleared and synchronised before t0, TIMES copies, then
cudaDeviceSynchronize before t1; any CUDA error exits non zero). With
PRAVAHA_KNOWN_WINDOW=1 it prints `WINDOW t0 t1` (wall clock ns, the clock of
the A-LEMS samples) around the work only. A-LEMS never changes the loop.

Steps:
  prepare   short probe batch (before the bracket's pre idle) -> per copy time,
            TIMES for the requested duration
  control   matched no copy control: zero copies with the program's pause set
            to the duration, so the same process, CUDA context and buffers are
            held; its window is [t0 - duration, t0]
  measure   one known_bytes run inside the measured window; completed bytes
            only on exit 0 with a WINDOW line
  evaluate  steadiness (5 s bins of pravaha copy_bytes anchored at t0) and
            observed copy bytes over [t0, t1] (edge intervals prorated)
The signed agreement error needs the idle brackets and is computed in analysis
from the stored observed bytes, known bytes, work window and idle runs.

Configuration (~/.alemsrc or shell; never hardcoded):
  ALEMS_COPY_SIZES_MIB        allowed sizes, comma list (default 16,256)
  ALEMS_COPY_PROBE_TIMES      copies in the probe batch (default 4)
  ALEMS_COPY_DURATION_TOL     allowed relative duration miss (default 0.10)
  ALEMS_COPY_STEADY_BIN_S     steadiness bin (default 5)
  ALEMS_COPY_STEADY_TOL       allowed bin deviation from the mean (default 0.05)
  ALEMS_COPY_BIN_COVERAGE     minimum covered fraction per bin (default 0.9)
  ALEMS_COPY_TIMEOUT_FACTOR   timeout = duration x factor + 60 s (default 3)
"""

import math
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from . import window

MIB = 1 << 20


def _env(name, default):
    # type: (str, str) -> str
    return os.environ.get(name) or default


def allowed_sizes():
    # type: () -> List[int]
    """Configured transfer sizes in MiB."""
    return [int(x) for x in _env("ALEMS_COPY_SIZES_MIB", "16,256").split(",") if x.strip()]


def run_known_bytes(direction, times, size_mib, timeout_s, pause_s=None):
    # type: (str, int, int, float, Optional[float]) -> Tuple[int, Optional[int], Optional[int], str]
    """One known_bytes process; returns (exit code, t0, t1, stderr tail)."""
    env = dict(os.environ, PRAVAHA_KNOWN_WINDOW="1")
    if pause_s is not None:
        env["PRAVAHA_KNOWN_PAUSE"] = str(pause_s)   # control: hold context and buffers this long
    p = subprocess.run([sys.executable, "-m", "pravaha_mem.known_bytes", direction, str(times), str(size_mib)],
                       env=env, capture_output=True, text=True, timeout=timeout_s)
    t0 = t1 = None
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "WINDOW":
            t0, t1 = int(parts[1]), int(parts[2])
    return p.returncode, t0, t1, (p.stderr or "")[-300:]


class CopyRateStep(StepKindABC):
    """Steady host to device or device to host copies of a known size."""

    KIND = "copy_rate"
    PARAMS = {
        "size_mib":    {"type": "number", "unit": "MiB", "required": True},
        "direction":   {"type": "string", "choices": ["h2d", "d2h"], "required": True},
        "duration_s":  {"type": "number", "unit": "s"},
        "memory_type": {"type": "string", "choices": ["pinned"]},
        "control":     {"type": "bool"},
    }
    ALEMS_PLUGIN_META = {"plugin_id": "pravaha_copy_rate", "family": "execution",
                         "extension_point": "alems.sequencer.steps", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        errors = super().validate(params)
        if params.get("size_mib") not in allowed_sizes():
            errors.append("copy_rate: size_mib %r not in ALEMS_COPY_SIZES_MIB %s"
                          % (params.get("size_mib"), allowed_sizes()))
        return errors

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        try:
            import pravaha_mem
            st = pravaha_mem.status()
        except Exception as exc:
            return Availability(False, "pravaha_missing", str(exc))
        if not st.get("ready"):
            return Availability(False, "pravaha_not_ready", "; ".join(st.get("fix") or []))
        return Availability(True)

    def prepare(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Probe batch sizing TIMES; controls run zero copies (same program, context and buffers)."""
        dur = float(step.get("duration_s") or 60)
        if step.get("control"):
            return {"times": 0, "probe_rate_bps": None, "duration_s": dur}
        n = int(_env("ALEMS_COPY_PROBE_TIMES", "4"))
        rc, t0, t1, err = run_known_bytes(step["direction"], n, int(step["size_mib"]), dur)
        if rc != 0 or t0 is None or t1 is None or t1 <= t0:
            raise RuntimeError("probe failed (exit %s): %s" % (rc, err))
        rate = n * step["size_mib"] * MIB / ((t1 - t0) / 1e9)
        return {"times": max(1, math.ceil(dur * rate / (step["size_mib"] * MIB))),
                "probe_rate_bps": rate, "probe_times": n, "duration_s": dur}

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        prep = ctx.get("prepared") or {}
        if "times" not in prep:
            raise StepFailed("prepare_failed", str(prep.get("prepare_error", "no probe result")))
        times, size, dur = prep["times"], int(step["size_mib"]), prep["duration_s"]
        timeout = dur * float(_env("ALEMS_COPY_TIMEOUT_FACTOR", "3")) + 60
        control = bool(step.get("control"))
        try:
            rc, t0, t1, err = run_known_bytes(step["direction"], times, size, timeout,
                                              pause_s=dur if control else None)
        except subprocess.TimeoutExpired:
            raise StepFailed("timeout", "known_bytes exceeded %.0f s" % timeout)
        if rc != 0 or t0 is None or t1 is None:
            raise StepFailed("workload_failed", "exit %s: %s" % (rc, err))
        if control:
            # Matched control: the held pause before the (empty) work, same process, context and buffers
            t1, t0 = t0, t0 - int(dur * 1e9)
        return self._results(step, prep, times, size, t0, t1)

    def _results(self, step, prep, times, size, t0, t1):
        """Stored values of a completed copy step (target rate NULL: no rate controller)."""
        actual = (t1 - t0) / 1e9
        done = times * size * MIB
        tol = float(_env("ALEMS_COPY_DURATION_TOL", "0.10"))
        return {"requested_bytes": (done, "B"), "completed_bytes": (done, "B"), "times": (times, None),
                "size_mib": (size, "MiB"), "direction": (step["direction"], None),
                "memory_type": ("pinned", None), "control": (bool(step.get("control")), None),
                "work_t0_ns": (t0, "ns"), "work_t1_ns": (t1, "ns"), "actual_duration_s": (actual, "s"),
                "requested_duration_s": (prep["duration_s"], "s"),
                "duration_ok": (step.get("control") or abs(actual - prep["duration_s"]) <= tol * prep["duration_s"], None),
                "achieved_rate_bps": (done / actual if actual > 0 and times else None, "B/s"),
                "probe_rate_bps": (prep.get("probe_rate_bps"), "B/s"), "target_rate_bps": (None, "B/s")}

    def evaluate(self, step, ctx, results, run):
        # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """Observed copy bytes over the work window and 5 s steadiness, from the run's traffic samples."""
        t0, t1 = results["work_t0_ns"][0], results["work_t1_ns"][0]
        samples = [s for r in (run.get("traffic") or []) if r.get("status") == "measured"
                   for s in r.get("samples") or []]
        if not samples:
            return {"observed_copy_bytes": (None, "B"), "steadiness": ("unknown", None)}
        observed = _bytes_in(samples, t0, t1)
        steady = "not_applicable" if results["control"][0] else _steadiness(samples, t0, t1)
        return {"observed_copy_bytes": (observed, "B"), "steadiness": (steady, None)}


def _bytes_in(samples, a, b):
    # type: (List[Dict[str, Any]], int, int) -> Optional[float]
    """copy_bytes over [a, b]; samples straddling an edge are prorated by overlap."""
    return window.bytes_in(samples, a, b, "copy_bytes")


def _steadiness(samples, t0, t1):
    # type: (List[Dict[str, Any]], int, int) -> str
    """steady, not_steady or unknown, from bins anchored at t0 (final bin under half dropped)."""
    return window.steadiness(samples, t0, t1, "copy_bytes", float(_env("ALEMS_COPY_STEADY_BIN_S", "5")),
                             float(_env("ALEMS_COPY_STEADY_TOL", "0.05")),
                             float(_env("ALEMS_COPY_BIN_COVERAGE", "0.9")))
