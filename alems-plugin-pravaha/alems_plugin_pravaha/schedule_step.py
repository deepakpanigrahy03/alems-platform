"""
alems_plugin_pravaha.schedule_step: a pravaha_mem schedule (Paper 5) as one measured
sequencer step.

The schedule (a JSON file of slots: idle, noop sham, kernel with a resident or
streaming footprint, copy, compute, overlap, square wave) runs in one process with one
CUDA context, so slots differ only in their work. The whole schedule is one measured
run; every slot and every square wave half carries its own wall clock window, so the
analysis (paper 5 pipeline, p5_analysis) allocates counter energy and observed traffic
to each window.

Steps:
  is_available  pravaha_mem importable and ready, schedule file valid
  measure       python -m pravaha_mem.schedule FILE inside the measured window; a
                failed check or exit fails the step
  evaluate      observed bytes per slot from the run's traffic samples, for the
                sample fields named in observed_fields (absent fields stay NULL)
Stored: setup window; slot_NN.* (name, kind, t0_ns, t1_ns, ops, payload_bytes,
check_ok); half_NNNN.* for square waves (slot, period, half, t0_ns, t1_ns, ops,
payload_bytes); slot_NN.obs_<field> after evaluate. Payload bytes are program counted.
"""
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from . import window


def run_schedule(path, device, timeout_s):
    # type: (str, int, float) -> Tuple[int, List[str], str]
    """One schedule process; returns (exit code, stdout lines, stderr tail)."""
    p = subprocess.run([sys.executable, "-m", "pravaha_mem.schedule", path, "--device", str(device)],
                       capture_output=True, text=True, timeout=timeout_s)
    return p.returncode, p.stdout.splitlines(), (p.stderr or "")[-300:]


class ScheduleLoadStep(StepKindABC):
    """Paper 5 workload schedule (pravaha_mem.schedule) as one measured step."""

    KIND = "schedule_load"
    PARAMS = {
        "schedule": {"type": "string", "required": True},
        "timeout_s": {"type": "number", "unit": "s", "required": True},
        "device": {"type": "number"},
        "observed_fields": {"type": "string"},
    }
    ALEMS_PLUGIN_META = {"plugin_id": "pravaha_schedule_load", "family": "execution",
                         "extension_point": "alems.sequencer.steps", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        """pravaha_mem with its schedule module, ready, and a valid schedule file."""
        try:
            import pravaha_mem
            from pravaha_mem import schedule
            st = pravaha_mem.status()
        except Exception as exc:
            return Availability(False, "pravaha_missing", str(exc))
        if not st.get("ready"):
            return Availability(False, "pravaha_not_ready", "; ".join(st.get("fix") or []))
        path = os.path.expanduser(step["schedule"])
        try:
            with open(path) as fh:
                schedule.validate(json.load(fh))
        except (OSError, ValueError) as exc:
            return Availability(False, "schedule_invalid", "%s: %s" % (path, exc))
        return Availability(True)

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """Run the schedule; any non zero exit or failed check fails the step."""
        from pravaha_mem.schedule import parse
        try:
            rc, lines, err = run_schedule(os.path.expanduser(step["schedule"]), int(step.get("device") or 0),
                                          float(step["timeout_s"]))
        except subprocess.TimeoutExpired:
            raise StepFailed("timeout", "schedule exceeded %s s" % step["timeout_s"])
        out = parse(lines)
        if rc != 0 or out["done"] is None:
            raise StepFailed("schedule_failed" if rc != 3 else "check_failed", "exit %s: %s" % (rc, err))
        return results(out, step["schedule"])

    def evaluate(self, step, ctx, results_in, run):
        # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """Observed bytes per slot window, for each named traffic sample field."""
        fields = [f.strip() for f in (step.get("observed_fields") or "").split(",") if f.strip()]
        samples = [s for r in (run.get("traffic") or []) if r.get("status") == "measured"
                   for s in r.get("samples") or []]
        if not fields or not samples:
            return {}
        out = {}
        n = int(results_in["n_slots"][0])
        for i in range(n):
            t0, t1 = results_in["slot_%02d.t0_ns" % i][0], results_in["slot_%02d.t1_ns" % i][0]
            for f in fields:
                v = window.bytes_in(samples, t0, t1, f)
                if v is not None:
                    out["slot_%02d.obs_%s" % (i, f)] = (v, "B")
        return out


def results(out, path):
    # type: (Dict[str, Any], str) -> Dict[str, Tuple[Any, Optional[str]]]
    """Stored values from the parsed schedule output; None values are left out (NULL)."""
    res = {"schedule_file": (path, None), "n_slots": (len(out["slots"]), None),
           "n_halves": (len(out["halves"]), None),
           "setup_t0_ns": (out["setup"]["t0"], "ns"), "setup_t1_ns": (out["setup"]["t1"], "ns"),
           "payload_bytes_total": (out["done"]["payload_bytes"], "B"),
           "checks_ok": (bool(out["done"]["checks_ok"]), None)}
    for s in out["slots"]:
        p = "slot_%02d." % s["index"]
        res.update({p + "name": (s["name"], None), p + "kind": (s["kind"], None),
                    p + "t0_ns": (s["t0"], "ns"), p + "t1_ns": (s["t1"], "ns"),
                    p + "ops": (s["ops"], None), p + "payload_bytes": (s["payload_bytes"], "B")})
        if s.get("check") is not None:
            res[p + "check_ok"] = (bool(s["check"]["ok"]), None)
        for k, v in (s.get("detail") or {}).items():
            res[p + k] = (v, None)
    for j, h in enumerate(out["halves"]):
        p = "half_%04d." % j
        res.update({p + "slot": (h["slot"], None), p + "period": (h["period"], None), p + "half": (h["half"], None),
                    p + "t0_ns": (h["t0"], "ns"), p + "t1_ns": (h["t1"], "ns"), p + "ops": (h["ops"], None),
                    p + "payload_bytes": (h["payload_bytes"], "B")})
    return res
