"""
core/sequencer/kinds.py: built in step kinds (idle, profile).

Each kind returns an outcome dict: status ('completed' | 'failed'),
reason_code, reason_detail, meas_start_utc_ns, meas_end_utc_ns, and, for
idle, extra_fn (store work done in the attempt's closing transaction).
Kinds never write the store themselves; the sequencer owns persistence.
Plugin kinds (alems_sdk.sequencer.StepKindABC) are discovered from the entry
point group alems.sequencer.steps; core never names them.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from core.sequencer.context import attempt_scope

logger = logging.getLogger(__name__)
IDLE_SAMPLES_ENV = "ALEMS_SEQ_IDLE_SAMPLES"
DEFAULT_IDLE_SAMPLES = 10


def _engine(config_loader):
    """EnergyEngine built exactly as the harness builds it (hardware config + settings)."""
    from core.energy_engine import EnergyEngine
    settings = config_loader.get_settings()
    engine_config = config_loader.get_hardware_config().copy()
    engine_config["settings"] = settings.__dict__ if hasattr(settings, "__dict__") else settings
    return EnergyEngine(engine_config)


def run_idle(step, ctx):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """
    Idle bracket side as a normal measured run (work = sleep for the duration):
    the same collectors as the treatment (energy, DCGM, traffic), so idle
    background rates exist for every quantity the treatment records.
    """
    from core.sequencer.benchmark import measured_run
    dur = float(step["params"]["duration_s"])
    out = measured_run(ctx, "idle:%s" % step["bracket_id"], lambda: time.sleep(dur), goal="idle bracket")
    return {"status": "completed", "exp_id": out["exp_id"],
            "meas_start_utc_ns": _run_bound(out, "start"), "meas_end_utc_ns": _run_bound(out, "end"),
            "results": {"idle_duration_s": (dur, "s")}}


def _run_bound(out, which):
    """Start or end ns of the stored run (wall clock, same as samples)."""
    ml = (out.get("result") or {}).get("ml_features") or {}
    return ml.get("start_time_ns") if which == "start" else ml.get("end_time_ns")


def run_profile(step, ctx):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """
    Run an `alems run` profile in this process inside the attempt scope; every
    run it persists is linked to the attempt in its own transaction.
    The treatment window is the envelope of the linked runs.
    """
    from core.cli.cmd_run import _delegate_to_runner
    profile = Path(step["params"]["profile"]).expanduser()
    if not profile.is_file():
        return {"status": "failed", "reason_code": "profile_missing", "reason_detail": str(profile)}
    with attempt_scope(ctx["attempt_id"]):
        rc = _delegate_to_runner(profile, list(step["params"].get("args") or []))
    runs = ctx["store"].linked_runs(ctx["attempt_id"])
    out = {"meas_start_utc_ns": min((r[1] for r in runs if r[1]), default=None),
           "meas_end_utc_ns": max((r[2] for r in runs if r[2]), default=None),
           "exp_id": ctx["store"].conn.execute(
               "SELECT exp_id FROM runs WHERE run_id = ?", (runs[0][0],)).fetchone()[0] if runs else None}
    if rc != 0:
        out.update(status="failed", reason_code="profile_exit", reason_detail="exit code %s" % rc)
    elif not runs:
        out.update(status="failed", reason_code="no_runs_linked",
                   reason_detail="profile completed but stored no run (save to the store enabled?)")
    else:
        out["status"] = "completed"
    return out


KINDS = {"idle": run_idle, "profile": run_profile}
PLUGIN_GROUP = "alems.sequencer.steps"
_PLUGINS = None  # type: Any


def plugin_kind_classes():
    # type: () -> Dict[str, Any]
    """StepKindABC classes registered by plugins, keyed by KIND (loaded once)."""
    global _PLUGINS
    if _PLUGINS is None:
        _PLUGINS = {}
        try:
            from importlib.metadata import entry_points
            eps = entry_points()
            group = eps.select(group=PLUGIN_GROUP) if hasattr(eps, "select") else eps.get(PLUGIN_GROUP, [])
        except Exception as exc:
            logger.warning("sequencer: plugin discovery failed: %s", exc)
            group = []
        for ep in group:
            _register_plugin(ep)
    return _PLUGINS


def _register_plugin(ep):
    """Load one entry point; duplicates and broken plugins are logged and refused."""
    try:
        cls = ep.load()
    except Exception as exc:
        logger.warning("sequencer: plugin %s not loadable: %s", ep.name, exc)
        return
    kind = getattr(cls, "KIND", "") or ep.name
    if kind in KINDS or kind in _PLUGINS:
        logger.warning("sequencer: duplicate step kind %r from %s refused", kind, ep.value)
        return
    _PLUGINS[kind] = cls


def _plugin_runner(cls):
    """Adapt a StepKindABC class: availability, measured run around measure(), evaluate."""
    def _run(step, ctx):
        from alems_sdk.sequencer import StepFailed
        from core.sequencer.benchmark import measured_run
        kind, params = cls(), step["params"]
        kctx = {"attempt_id": ctx["attempt_id"], "cancelled": lambda: False,
                "prepared": (ctx.get("prepared") or {}).get(step["bracket_id"])}
        avail = kind.is_available(params, kctx)   # rechecked at dispatch
        if not avail.ok:
            return {"status": "skipped", "reason_code": avail.code, "reason_detail": avail.detail}
        if getattr(cls, "MEASUREMENT", "measured") == "profiling":
            return _profiling_outcome(kind, params, dict(kctx, db=ctx["db"]), StepFailed)
        kind.setup(params, kctx)
        try:
            out = measured_run(ctx, "%s:%s" % (cls.KIND, step["condition"]),
                               lambda: kind.measure(params, kctx), goal=step["condition"])
        finally:
            kind.teardown(params, kctx)
        return _plugin_outcome(kind, params, kctx, out, StepFailed)
    return _run


def _profiling_outcome(kind, params, kctx, step_failed):
    """Profiling kinds: measure() directly, no measured run and no collectors; its own tables keep the data."""
    kind.setup(params, kctx)
    try:
        results = kind.measure(params, kctx)
    except step_failed as exc:
        return {"status": "failed", "reason_code": exc.code, "reason_detail": str(exc)}
    finally:
        kind.teardown(params, kctx)
    return {"status": "completed", "results": results}


def _plugin_outcome(kind, params, kctx, out, step_failed):
    """Results of measure() plus evaluate(); a StepFailed or error marks the step failed."""
    results = dict(out["value"] or {})
    if out["error"] is None:
        results.update(kind.evaluate(params, kctx, results, out["result"]) or {})
    outcome = {"exp_id": out["exp_id"], "results": results,
               "meas_start_utc_ns": _run_bound(out, "start"), "meas_end_utc_ns": _run_bound(out, "end")}
    err = out["error"]
    if err is None:
        outcome["status"] = "completed"
    else:
        code = getattr(err, "code", None) if isinstance(err, step_failed) else "exception"
        outcome.update(status="failed", reason_code=code or "step_failed", reason_detail=str(err))
    return outcome


def prepare_bracket(step, ctx):
    # type: (Dict[str, Any], Dict[str, Any]) -> Any
    """Plugin prepare() of a treatment, called before its bracket's pre idle; None for built ins."""
    cls = plugin_kind_classes().get(step["kind"])
    if cls is None:
        return None
    return cls().prepare(step["params"], {"attempt_id": None, "cancelled": lambda: False})


def all_kinds():
    # type: () -> Dict[str, Any]
    """Built in kinds plus plugin kinds (built ins cannot be overridden)."""
    out = {k: _plugin_runner(c) for k, c in plugin_kind_classes().items()}
    out.update(KINDS)
    return out


def validate_plan(plan):
    # type: (Dict[str, Any]) -> List[str]
    """Parameter errors of plugin steps, found before any step runs."""
    classes = plugin_kind_classes()
    errors = []
    for block in plan["blocks"]:
        cls = classes.get(block["treatment"].get("kind"))
        if cls is not None:
            errors.extend("block %s: %s" % (block["condition"], e) for e in cls.validate(block["treatment"]))
    return errors


def thermal_reader(config_loader):
    """Platform thermal reader from the reader factory; None when unavailable."""
    if config_loader is None:
        return None
    try:
        from core.readers.factory import ReaderFactory
        settings = config_loader.get_settings()
        cfg = config_loader.get_hardware_config().copy()
        cfg["settings"] = settings.__dict__ if hasattr(settings, "__dict__") else settings
        return ReaderFactory.get_thermal_reader(cfg)
    except Exception as exc:  # no reader: gate records not_available
        logger.warning("sequencer: thermal reader unavailable: %s", exc)
        return None
