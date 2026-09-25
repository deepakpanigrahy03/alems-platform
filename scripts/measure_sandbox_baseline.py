"""
measure_sandbox_baseline.py -- called by _initialize_store during sandbox create.
Measures or confirms idle baseline for the sandbox store at ALEMS_STORE.
Must be run from the engine root with ALEMS_STORE set.
"""
import sys
from pathlib import Path

sys.path.insert(0, ".")

from core.config_loader import ConfigLoader
from core.utils.baseline_manager import BaselineManager


def main():
    # type: () -> None
    cl = ConfigLoader()
    hw = cl.get_hardware_config()

    if hw.get("energy_measurement") != "direct":
        print("baseline skipped: energy_measurement=" + hw.get("energy_measurement", "unknown"))
        return

    mgr = BaselineManager()
    existing = mgr.get_latest()
    if existing:
        print("existing baseline found: " + existing.baseline_id)
        return

    print("measuring idle baseline (10s)...")
    from core.energy_engine import EnergyEngine
    engine = EnergyEngine(hw)
    b = engine.measure_idle_baseline(duration_seconds=10, num_samples=1, pre_wait_seconds=5)
    if b and getattr(b, "package_power_w", 0) != 0.0:
        mgr.save(b)
        print("baseline saved: " + b.baseline_id)
    else:
        print("baseline measurement failed or returned zero")


if __name__ == "__main__":
    main()
