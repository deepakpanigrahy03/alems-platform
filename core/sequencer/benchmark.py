"""
core/sequencer/benchmark.py: run any step's work as a normal measured run.

Why: an idle bracket or a benchmark (for example a steady copy) must be
measured exactly like an LLM run: same energy window, same collectors (rails,
DCGM, GPU traffic, serving snapshots, temperatures), same persistence and the
same run to attempt link. Instead of a second measurement path, the work is
wrapped in an executor with the interface run_linear() uses
(config["model_id"], provider, execute(prompt) -> dict), and the run is
stored with save_single(). run_linear reads the executor result with .get()
and defaults, so LLM fields (tokens, latency) stay NULL for this work.

Runs belong to one experiment per step attempt with experiment_type
'benchmark' and workflow_type 'linear' (schema v127).
"""

import argparse
import logging
import time
from typing import Any, Callable, Dict, Optional

from core.sequencer.context import attempt_scope

logger = logging.getLogger(__name__)
PROVIDER_LABEL = "benchmark"


class BenchmarkExecutor:
    """Executor wrapper: execute() runs the work function inside run_linear's window."""

    def __init__(self, label, work_fn):
        # type: (str, Callable[[], Any]) -> None
        self.config = {"model_id": label}
        self.provider = PROVIDER_LABEL
        self._work = work_fn
        self.value = None   # what the work returned
        self.error = None   # type: Optional[BaseException]

    def execute(self, prompt):
        # type: (str) -> Dict[str, Any]
        """Run the work once; an exception is kept, not raised, so the run is still stored."""
        t0 = time.perf_counter()
        try:
            self.value = self._work()
        except Exception as exc:   # kept: the window closes and the run is persisted
            self.error = exc
        return {"execution_time_ms": (time.perf_counter() - t0) * 1000.0, "tokens": {},
                "pending_interactions": []}


def _session_runtime(ctx):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Harness, runner, hardware and environment ids, built once per session and cached in ctx."""
    if "bench" not in ctx:
        from core.execution.experiment_runner import ExperimentRunner
        from core.execution.harness import ExperimentHarness
        loader, db = ctx["config_loader"], ctx["db"]
        runner = ExperimentRunner(loader, argparse.Namespace())
        ctx["bench"] = {"harness": ExperimentHarness(loader), "runner": runner,
                        "hw_id": db.insert_hardware(runner.get_hardware_info()),
                        "env_id": runner.setup_environment(db)}
    return ctx["bench"]


def measured_run(ctx, label, work_fn, goal=None):
    # type: (Dict[str, Any], str, Callable[[], Any], Optional[str]) -> Dict[str, Any]
    """
    Run work_fn inside the standard measurement window and store it as a run
    linked to the current attempt. Returns exp_id, run_id, the in memory run
    result (samples), the work's return value and any work error.
    """
    rt, db = _session_runtime(ctx), ctx["db"]
    exp_id = rt["runner"].create_experiment(
        db, label, label, PROVIDER_LABEL, {"model_id": label}, None, 1, rt["hw_id"], rt["env_id"],
        experiment_type="benchmark", experiment_goal=goal, workflow_mode="linear")
    executor = BenchmarkExecutor(label, work_fn)
    result = rt["harness"].run_linear(executor, "", task_id=label, is_cloud=False, run_number=1)
    with attempt_scope(ctx["attempt_id"]):
        run_id = rt["runner"].save_single(db, exp_id, rt["hw_id"], result, 1, "linear")
    return {"exp_id": exp_id, "run_id": run_id, "result": result,
            "value": executor.value, "error": executor.error}
