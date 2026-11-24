"""
core/utils/baseline_row.py: idle_baselines row from a BaselineMeasurement.

One function shared by the experiment runner and the sequencer idle step, so
both store baselines with identical semantics. Behaviour kept exactly as the
runner had it (missing PACKAGE/CORE/UNCORE/DRAM power stored as 0, a known
MIC-1 deviation logged in the 45.4c handover backlog, not changed here).
"""

from typing import Any, Dict


def baseline_row(b):
    # type: (Any) -> Dict[str, Any]
    """Dict for DatabaseManager.insert_baseline()."""
    metadata = b.metadata or {}
    return {
        "baseline_id": b.baseline_id,
        "timestamp": b.timestamp,
        # canonical keys after v61: PACKAGE, CORE, UNCORE, DRAM
        "package_power_watts": b.power_watts.get("PACKAGE", 0),
        "core_power_watts":    b.power_watts.get("CORE", b.power_watts.get("CPU_P", 0)),
        "uncore_power_watts":  b.power_watts.get("UNCORE", 0),
        "dram_power_watts":    b.power_watts.get("DRAM", 0),
        "duration_seconds": b.duration_seconds,
        "sample_count": b.sample_count,
        "package_std": b.std_dev_watts.get("PACKAGE", b.std_dev_watts.get("package-0")),
        "core_std":    b.std_dev_watts.get("CORE", b.std_dev_watts.get("CPU_P", b.std_dev_watts.get("core"))),
        "uncore_std":  b.std_dev_watts.get("UNCORE", b.std_dev_watts.get("uncore")),
        "dram_std":    b.std_dev_watts.get("DRAM", b.std_dev_watts.get("dram")),
        "governor": metadata.get("governor"),
        "turbo": metadata.get("turbo"),
        "background_cpu": metadata.get("background_cpu"),
        "process_count": metadata.get("process_count"),
        "method": b.method,
    }
