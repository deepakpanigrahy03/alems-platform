"""
alems_plugin_pravaha.kernel_step: a GPU kernel reading or writing a known amount of data in
a chosen memory placement, as a measured sequencer step (Paper 5 arm P5-A).

Author: Deepak Panigrahy

Workload: pravaha_mem.known_kernel (pravaha >= 1.1.0). Pravaha owns the CUDA code, its
compilation (NVRTC) and the checksum; A-LEMS owns conditions, windows, baselines and links.

Controlled: placement (allocation API: device, pinned, managed, pageable), init (gpu or cpu),
prefetch (managed only: none or device; not_applicable otherwise), op (read or write), size,
duration. Observed: energy (the measured run), device wide traffic at the pravaha counters,
memory attributes before and after (requests and policy, never residency).

Steps:
  prepare   probe batch -> seconds per pass -> TIMES for the duration
  measure   one known_kernel run inside the measured window; control = held window of the
            same duration with the buffer prepared and no passes (logical bytes 0)
  evaluate  observed kernel read miss and write bytes over [t0, t1], observed bytes per
            logical byte of the operation, steadiness of that traffic
Primary outcome (analysis, 45.5): incremental joules per logical byte by energy domain;
observed traffic is reported next to it, never used as the energy denominator.
residency_changed is NULL: the attributes cannot establish page movement.

Configuration (~/.alemsrc or shell; never hardcoded):
  ALEMS_KRW_SIZES_MIB        allowed sizes, comma list (default 16,256)
  ALEMS_KRW_PROBE_TIMES      passes in the probe batch (default 8)
  ALEMS_KRW_DURATION_TOL     allowed relative duration miss (default 0.10)
  ALEMS_KRW_TIMEOUT_FACTOR   timeout = duration x factor + 60 s (default 3)
  ALEMS_KRW_INIT             default initialiser (default gpu)
  ALEMS_KRW_STEADY_BIN_S, ALEMS_KRW_STEADY_TOL, ALEMS_KRW_BIN_COVERAGE (defaults 5, 0.05, 0.9)
"""

import json
import math
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from .window import bytes_in, measured_samples, steadiness

MIB = 1 << 20
COLUMN = {"read": "kernel_read_miss_bytes", "write": "kernel_write_bytes"}


def _env(name, default):
    # type: (str, str) -> str
    return os.environ.get(name) or default


def allowed_sizes():
    # type: () -> List[int]
    return [int(x) for x in _env("ALEMS_KRW_SIZES_MIB", "16,256").split(",") if x.strip()]


def _known_kernel():
    from pravaha_mem import known_kernel
    return known_kernel


def run_kernel(step, times, timeout_s, hold_s=None):
    # type: (Dict[str, Any], int, float, Optional[float]) -> Dict[str, Any]
    """One known_kernel process with the step's controlled settings."""
    pf = step.get("prefetch") if step["placement"] == "managed" else None
    return _known_kernel().run(step["placement"], step["op"], times, int(step["size_mib"]), prefetch=pf,
                               init=step.get("init") or _env("ALEMS_KRW_INIT", "gpu"), hold_s=hold_s,
                               timeout=timeout_s)


class KernelRWStep(StepKindABC):
    """Steady kernel reads or writes of a known buffer in one memory placement."""

    KIND = "kernel_rw"
    PARAMS = {
        "placement":  {"type": "string", "choices": ["device", "pinned", "managed", "pageable"], "required": True},
        "op":         {"type": "string", "choices": ["read", "write"], "required": True},
        "size_mib":   {"type": "number", "unit": "MiB", "required": True},
        "duration_s": {"type": "number", "unit": "s"},
        "prefetch":   {"type": "string", "choices": ["none", "device"]},
        "init":       {"type": "string", "choices": ["gpu", "cpu"]},
        "control":    {"type": "bool"},
    }
    ALEMS_PLUGIN_META = {"plugin_id": "pravaha_kernel_rw", "family": "execution",
                         "extension_point": "alems.sequencer.steps", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        errors = super().validate(params)
        if params.get("size_mib") not in allowed_sizes():
            errors.append("kernel_rw: size_mib %r not in ALEMS_KRW_SIZES_MIB %s"
                          % (params.get("size_mib"), allowed_sizes()))
        if params.get("placement") == "managed" and params.get("prefetch") is None:
            errors.append("kernel_rw: managed placement needs prefetch none or device")
        if params.get("placement") != "managed" and params.get("prefetch") is not None:
            errors.append("kernel_rw: prefetch applies to managed placement only")
        return errors

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        try:
            sup = _known_kernel().query()["supported"][step["placement"]]
        except Exception as exc:
            return Availability(False, "known_kernel_unavailable", str(exc))
        return Availability(True) if sup["ok"] else Availability(False, "placement_unsupported", sup["reason"] or "")

    def prepare(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Probe batch sizing TIMES; a control needs no probe."""
        dur = float(step.get("duration_s") or 60)
        if step.get("control"):
            return {"times": 0, "probe_pass_s": None, "duration_s": dur}
        n = int(_env("ALEMS_KRW_PROBE_TIMES", "8"))
        r = run_kernel(step, n, dur)
        if r["returncode"] != 0 or r["t0"] is None or r["t1"] <= r["t0"]:
            raise RuntimeError("probe failed (exit %s): %s" % (r["returncode"], r["stderr_tail"]))
        per_pass = (r["t1"] - r["t0"]) / 1e9 / n
        return {"times": max(1, math.ceil(dur / per_pass)), "probe_pass_s": per_pass,
                "probe_times": n, "duration_s": dur}

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        prep = ctx.get("prepared") or {}
        if "times" not in prep:
            raise StepFailed("prepare_failed", str(prep.get("prepare_error", "no probe result")))
        dur, control = prep["duration_s"], bool(step.get("control"))
        timeout = dur * float(_env("ALEMS_KRW_TIMEOUT_FACTOR", "3")) + 60
        try:
            r = run_kernel(step, prep["times"], timeout, hold_s=dur if control else None)
        except subprocess.TimeoutExpired:
            raise StepFailed("timeout", "known_kernel exceeded %.0f s" % timeout)
        if r["returncode"] == 3:
            raise StepFailed("check_failed", json.dumps(r.get("check")))
        if r["returncode"] != 0 or r["t0"] is None or r["check"] is None:
            raise StepFailed("workload_failed", "exit %s: %s" % (r["returncode"], r["stderr_tail"]))
        return self._results(step, prep, r, control)

    def _results(self, step, prep, r, control):
        chk, actual = r["check"], (r["t1"] - r["t0"]) / 1e9
        tol = float(_env("ALEMS_KRW_DURATION_TOL", "0.10"))
        logical = chk["logical_bytes"]
        return {"logical_bytes": (logical, "B"), "aux_write_bytes": (chk["aux_write_bytes"], "B"),
                "times": (0 if control else prep["times"], None), "size_mib": (int(step["size_mib"]), "MiB"),
                "placement": (step["placement"], None), "op": (step["op"], None),
                "prefetch": (step.get("prefetch") or "not_applicable", None),
                "init": (step.get("init") or _env("ALEMS_KRW_INIT", "gpu"), None), "control": (control, None),
                "work_t0_ns": (r["t0"], "ns"), "work_t1_ns": (r["t1"], "ns"),
                "first_pass_t1_ns": (r["first_t1"], "ns"),
                "actual_duration_s": (actual, "s"), "requested_duration_s": (prep["duration_s"], "s"),
                "duration_ok": (abs(actual - prep["duration_s"]) <= tol * prep["duration_s"], None),
                "achieved_logical_rate_bps": (logical / actual if actual > 0 and logical else None, "B/s"),
                "probe_pass_s": (prep.get("probe_pass_s"), "s"), "check": ("ok" if chk["ok"] else "fail", None),
                "checksum": (str(chk["checksum"]), None),
                "memory_attributes_before": (json.dumps(r["attrs_before"], sort_keys=True), None),
                "memory_attributes_after": (json.dumps(r["attrs_after"], sort_keys=True), None),
                "build": (json.dumps({k: chk.get(k) for k in ("arch", "runtime_version", "blocks", "threads")},
                                     sort_keys=True), None),
                "residency_changed": (None, None)}

    def evaluate(self, step, ctx, results, run):
        # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """Observed bytes of the operation's counter over the window, per logical byte, steadiness."""
        t0, t1 = results["work_t0_ns"][0], results["work_t1_ns"][0]
        samples = measured_samples(run)
        out = {"observed_read_miss_bytes": (None, "B"), "observed_write_bytes": (None, "B"),
               "observed_per_logical_byte": (None, None), "traffic_steadiness": ("unknown", None)}
        if not samples:
            return out
        rd = bytes_in(samples, t0, t1, COLUMN["read"])
        wr = bytes_in(samples, t0, t1, COLUMN["write"])
        obs = rd if step["op"] == "read" else wr
        logical = results["logical_bytes"][0]
        out.update(observed_read_miss_bytes=(rd, "B"), observed_write_bytes=(wr, "B"),
                   observed_per_logical_byte=(obs / logical if obs is not None and logical else None, None))
        if not results["control"][0]:
            st = steadiness(samples, t0, t1, COLUMN[step["op"]], float(_env("ALEMS_KRW_STEADY_BIN_S", "5")),
                            float(_env("ALEMS_KRW_STEADY_TOL", "0.05")), float(_env("ALEMS_KRW_BIN_COVERAGE", "0.9")))
            out["traffic_steadiness"] = (st, None)
        else:
            out["traffic_steadiness"] = ("not_applicable", None)
        return out
