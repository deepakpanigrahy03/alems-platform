"""
matmul_load step kind: fixed duty cycle matrix multiply, no model server.

Runs the repository's matmul load script (one copy of the load code, shared
with the X1 update rate script) under an interpreter that has torch with CUDA.
Attended only: the plan must set attended: true. is_available() is a
preflight temperature check; the shared thermal guard still decides
admission at dispatch. During the step the GPU temperature is watched and the
step fails (thermal_abort) at abort_temp_c.
"""
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from alems_plugin_workload import __version__

SUMMARY_RE = re.compile(r"iters=(\d+) busy_s=([\d.]+) wall_s=([\d.]+) duty=([\d.]+)")


def gpu_temp_c():
    # type: () -> Optional[float]
    """GPU temperature from nvidia-smi; None when it cannot be read."""
    cmd = ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        return float(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _stop(proc):
    # type: (subprocess.Popen) -> str
    """Terminate the load (it prints its summary on SIGTERM) and return its output."""
    if proc.poll() is None:
        proc.terminate()
    try:
        out, _ = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out or ""


class MatmulLoadStep(StepKindABC):
    """Controlled compute load as a measured sequencer step."""

    KIND = "matmul_load"
    ALEMS_PLUGIN_META = {"name": "workload", "version": __version__}
    PARAMS = {
        "duration_s": {"type": "number", "unit": "s", "required": True},
        "python": {"type": "string", "required": True},
        "script": {"type": "string", "required": True},
        "n": {"type": "number", "required": True},
        "dtype": {"type": "string", "choices": ["bfloat16", "float16", "float32"], "required": True},
        "duty": {"type": "number", "required": True},
        "period_ms": {"type": "number", "unit": "ms", "required": True},
        "seed": {"type": "number", "required": True},
        "max_start_temp_c": {"type": "number", "unit": "C", "required": True},
        "abort_temp_c": {"type": "number", "unit": "C", "required": True},
        "check_interval_s": {"type": "number", "unit": "s", "required": True},
        "attended": {"choices": [True], "required": True},
    }

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        """Attended, interpreter and script present, GPU below the start limit."""
        if step.get("attended") is not True:
            return Availability(False, "attended_only", "set attended: true and run attended")
        if not (Path(step["python"]).is_file() or shutil.which(step["python"])):
            return Availability(False, "python_missing", step["python"])
        if not Path(step["script"]).expanduser().is_file():
            return Availability(False, "script_missing", step["script"])
        temp = gpu_temp_c()
        if temp is None:
            return Availability(False, "temp_unreadable", "nvidia-smi")
        if temp > float(step["max_start_temp_c"]):
            return Availability(False, "gpu_too_hot", "%.0f C > %.0f C" % (temp, step["max_start_temp_c"]))
        return Availability(True)

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Run the load for duration_s, watching temperature; results include achieved duty."""
        cmd = [step["python"], str(Path(step["script"]).expanduser()), "--n", str(step["n"]),
               "--dtype", step["dtype"], "--duty", str(step["duty"]),
               "--period-ms", str(step["period_ms"]), "--duration-s", str(step["duration_s"]),
               "--seed", str(step["seed"])]
        t0 = time.time_ns()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        reason = self._watch(step, ctx, proc)
        out = _stop(proc)
        t1 = time.time_ns()
        if reason:
            raise StepFailed(reason[0], reason[1])
        if proc.returncode != 0:
            raise StepFailed("load_exit", "exit %s: %s" % (proc.returncode, out.strip()[-200:]))
        return self._results(out, t0, t1)

    def _watch(self, step, ctx, proc):
        # type: (Dict[str, Any], Dict[str, Any], subprocess.Popen) -> Optional[tuple]
        """Poll until the load exits; return (code, detail) to abort, else None."""
        while proc.poll() is None:
            time.sleep(float(step["check_interval_s"]))
            temp = gpu_temp_c()
            if temp is None or temp >= float(step["abort_temp_c"]):
                return ("thermal_abort", "gpu temperature %s C (limit %s C)" % (temp, step["abort_temp_c"]))
            if ctx["cancelled"]():
                return ("cancelled", "sequencer asked the step to stop")
        return None

    @staticmethod
    def _results(out, t0, t1):
        # type: (str, int, int) -> Dict[str, Any]
        """Parse the load's summary line; a missing summary fails the step."""
        m = SUMMARY_RE.search(out)
        if not m:
            raise StepFailed("no_summary", out.strip()[-200:])
        return {"iters": (int(m.group(1)), None), "busy_s": (float(m.group(2)), "s"),
                "wall_s": (float(m.group(3)), "s"), "achieved_duty": (float(m.group(4)), None),
                "work_t0_ns": (t0, "ns"), "work_t1_ns": (t1, "ns")}
