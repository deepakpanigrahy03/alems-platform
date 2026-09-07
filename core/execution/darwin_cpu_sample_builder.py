"""
core/execution/darwin_cpu_sample_builder.py
============================================
Helper: build one cpu_samples row from KPerfPMUReader PerformanceCounters
on Apple Silicon (Darwin arm64).

Mirrors arm_cpu_sample_builder.py exactly — one summary row per run.
On x86, turbostat writes N continuous rows. On Darwin, KPerfPMUReader
returns aggregate PerformanceCounters for the full measurement window.
aggregate_hardware_metrics ETL uses SUM() so one row produces identical
ETL output to N rows summing to the same values.

PAC-2: No platform-conditional imports — caller guards with
       platform.system() == 'Darwin' before calling this function.
EEI:   No DB access — returns a dict only.
DC-1:  ~30% comments explaining WHY, not WHAT.
PVC-1: Python 3.9 compatible — no walrus operator, no 3.10+ syntax.
"""

import logging
import time as _time

logger = logging.getLogger(__name__)


def _build_darwin_cpu_sample_row(run_id, result):
    # type: (int, dict) -> dict
    """
    Build a single cpu_samples row from KPerfPMUReader PerformanceCounters.

    Extracts PerformanceCounters from result dict returned by
    EnergyEngine.stop_measurement() on Darwin arm64. The perf counters
    are stored under result['perf_counters'] or result['raw_energy'].perf
    following the same convention as arm_cpu_sample_builder.

    Frequency comes from turbostat_data summary (IOReportCPUFreqReader).
    cpu_util comes from cpu_active_ratio * 100 (populated by IOReport).

    IPC: available from KPerfPMUReader (instructions/cycles).
    L2/L3 cache misses: NULL — not in a14.plist (hardware limitation).

    Args:
        run_id: DB run_id for traceability.
        result: dict from stop_measurement() or linear_result.

    Returns:
        dict ready for insert_cpu_samples(), or None if no counter data.
    """
    # perf data lives in derived_energy.performance (harness puts it there)
    derived = result.get("derived_energy") or {}
    perf = derived.get("performance", {}) if isinstance(derived, dict) else {}

    # ml_features also has instructions/cycles directly
    ml = result.get("ml_features") or {}

    instructions = perf.get("instructions") or ml.get("instructions") or 0
    cycles = perf.get("cycles") or 0
    cache_misses = perf.get("cache_misses") or None
    ipc = perf.get("ipc") or None
    l1d_misses = perf.get("cache_misses") or None

    if not instructions and not cycles:
        # No PMU data available for this run — log at WARNING for debug
        import json as _json
        print(f"BUILDER NONE: run_id={run_id} derived_keys={list(derived.keys()) if derived else 'EMPTY'} perf={perf} ml_instr={ml.get('instructions')}")
        return None

    if ipc is None and cycles and cycles > 0:
        ipc = instructions / cycles

    # Frequency and cpu_active_ratio from ml_features (populated by IOReport)
    freq_mean = ml.get("frequency_mhz") or 0.0
    cpu_active_ratio = ml.get("cpu_active_ratio")
    cpu_util = (cpu_active_ratio * 100.0) if cpu_active_ratio is not None else None

    now_ns = _time.time_ns()

    return {
        "run_id":            run_id,
        "timestamp_ns":      now_ns,
        # sample_start_ns and sample_end_ns patched by caller from run record
        "sample_start_ns":   None,
        "sample_end_ns":     None,
        "interval_ns":       None,
        # Frequency from IOReportCPUFreqReader
        "cpu_busy_mhz":      freq_mean or None,
        "cpu_avg_mhz":       freq_mean or None,
        # cpu_util from IOReport active ratio converted to percent
        "cpu_util_percent":  cpu_util,
        # IPC from KPerfPMUReader — only field cpu_samples schema accepts from PMU
        "ipc":               ipc,
        # L1D misses from KPerfPMUReader — in schema as l1d_cache_misses
        "l1d_cache_misses":  l1d_misses if l1d_misses else None,
        # L2/L3 not in a14.plist — MIC-1: NULL not zero
        "l2_cache_misses":   None,
        "l3_cache_misses":   None,
        # package_power: IOKit provides energy not instantaneous power here
        "package_power":     None,
    }
