"""
measure_sandbox_baseline.py -- called by _initialize_store during sandbox create.
Measures or confirms the idle baseline for the store at ALEMS_STORE.
Run from the engine root with ALEMS_STORE set.

B28/B30: uses the same baseline settings as experiments (app_settings.yaml,
section baseline: duration_seconds, num_samples, pre_wait_seconds, measure_gpu),
never a single 10 s sample. A saved baseline is reused unless --force is given.

Usage:
    python3 scripts/measure_sandbox_baseline.py            # reuse a valid one, else measure
    python3 scripts/measure_sandbox_baseline.py --force    # always measure a new one
"""
import argparse
import sys

sys.path.insert(0, ".")

from core.config_loader import ConfigLoader
from core.utils.baseline_manager import BaselineManager

# Fallbacks only if app_settings.yaml has no baseline section; B28 needs >= 3 samples
_DEFAULTS = {"duration_seconds": 30, "num_samples": 3, "pre_wait_seconds": 10, "measure_gpu": True}


def _baseline_settings(cl):
    # type: (ConfigLoader) -> dict
    """
    Baseline settings from app_settings.yaml (same values experiments use).

    Returns:
        dict with duration_seconds, num_samples, pre_wait_seconds, measure_gpu.
    """
    cfg = {}
    for getter in ("get_app_settings", "get_settings", "get_app_config"):
        fn = getattr(cl, getter, None)
        if callable(fn):
            try:
                cfg = (fn() or {}).get("baseline", {}) or {}
                break
            except Exception as e:
                print("warning: reading baseline settings via %s failed: %s" % (getter, e))
    out = dict(_DEFAULTS)
    out.update({k: cfg[k] for k in _DEFAULTS if k in cfg})
    return out


def main():
    # type: () -> int
    """Measure or confirm the idle baseline. Returns a process exit code."""
    ap = argparse.ArgumentParser(description="Measure or confirm the idle baseline for ALEMS_STORE")
    ap.add_argument("--force", action="store_true", help="measure a new baseline even if one exists")
    args = ap.parse_args()

    cl = ConfigLoader()
    hw = cl.get_hardware_config()
    if hw.get("energy_measurement") != "direct":
        print("baseline skipped: energy_measurement=" + str(hw.get("energy_measurement", "unknown")))
        return 0

    mgr = BaselineManager()
    existing = mgr.get_latest()
    if existing and not args.force:
        print("existing baseline found: %s (samples %s)"
              % (existing.baseline_id, getattr(existing, "sample_count", "?")))
        return 0

    s = _baseline_settings(cl)
    print("measuring idle baseline: %ss x %s samples after %ss wait; keep the machine idle"
          % (s["duration_seconds"], s["num_samples"], s["pre_wait_seconds"]))
    from core.energy_engine import EnergyEngine
    engine = EnergyEngine(hw)
    try:
        b = engine.measure_idle_baseline(
            duration_seconds=s["duration_seconds"], num_samples=s["num_samples"],
            pre_wait_seconds=s["pre_wait_seconds"], force_remeasure=True,
            measure_gpu=s["measure_gpu"])
    except Exception as e:
        # B28 rejection or reader failure: nothing saved, report and fail
        print("baseline measurement failed: %s" % e)
        return 1
    if not b or getattr(b, "package_power_w", 0) == 0.0:
        print("baseline measurement failed or returned zero; nothing saved")
        return 1
    mgr.save(b)
    print("baseline saved: %s  package %.2f W  samples %s"
          % (b.baseline_id, b.package_power_w, getattr(b, "sample_count", "?")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
