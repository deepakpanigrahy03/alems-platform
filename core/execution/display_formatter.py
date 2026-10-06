"""
Hardware and comparison displays for run_experiment and test_harness (user output).

39.5.2e: every display goes through the console renderer (stdout, plain text,
TTY color only, silent with --json and quiet). Function names and arguments are
unchanged; the values shown are unchanged; emoji indicators became words.
Called only after measurement windows close.
"""
import logging
import socket
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from scipy import stats as scipy_stats

from core.analysis.energy_analyzer import EnergyAnalyzer
from core.config_loader import ConfigLoader
from core.database.manager import DatabaseManager
from core.energy_engine import EnergyEngine
from core.execution.base import calc_stats
from core.observability.console import get_console
from core.sustainability.calculator import SustainabilityCalculator
from core.utils.baseline_manager import BaselineManager

logger = logging.getLogger(__name__)


def _level(value, high, moderate):
    """Map a value to a styled HIGH, MODERATE, LOW label (was colored emoji)."""
    con = get_console()
    if value > high:
        return con.style("HIGH", "red")
    if value > moderate:
        return con.style("MODERATE", "yellow")
    return con.style("LOW", "green")


def _yes_no(flag):
    """Render a truthy flag as YES or NO."""
    return "YES" if flag else "NO"


def _show_energy_perf(con, derived):
    """RAPL energy domains (Req 1.1, 1.3) and performance counters (Req 1.5 and others)."""
    energy_uj = derived.get("energy_uj", {})
    con.line("RAPL energy (Req 1.1)", indent=1)
    con.kv("package", "%.3f J" % (energy_uj.get("package", 0) / 1e6), indent=2)
    con.kv("core", "%.3f J" % (energy_uj.get("core", 0) / 1e6), indent=2)
    uncore = energy_uj.get("uncore", 0)
    if uncore > 0:
        con.kv("uncore", "%.3f J (includes GPU if no separate GPU domain)" % (uncore / 1e6), indent=2)
    dram = energy_uj.get("dram")
    if dram:
        con.kv("dram", "%.3f J" % (dram / 1e6), indent=2)

    perf = derived.get("performance", {})
    con.line("performance counters", indent=1)
    con.kv("instructions", "{:,}".format(perf.get("instructions", 0)), indent=2)
    con.kv("cycles", "{:,}".format(perf.get("cycles", 0)), indent=2)
    con.kv("ipc", "%.2f" % perf.get("ipc", 0), indent=2)
    con.kv("cache references", "{:,}".format(perf.get("cache_references", 0)), indent=2)
    con.kv("cache misses", "{:,}".format(perf.get("cache_misses", 0)), indent=2)
    cache_refs = perf.get("cache_references", 1)
    miss_rate = (perf.get("cache_misses", 0) / cache_refs) if cache_refs > 0 else 0
    con.kv("cache miss rate", "%.2f%%" % (miss_rate * 100), indent=2)
    con.kv("page faults", "{:,} (major {}, minor {})".format(
        perf.get("page_faults", 0), perf.get("major_page_faults", 0),
        perf.get("minor_page_faults", 0)), indent=2)


def _show_thermal(con, run, derived):
    """Thermal readings, heat flux, timeline, validity (Req 1.9)."""
    thermal = derived.get("thermal", {})
    con.line("thermal (Req 1.9)", indent=1)
    pkg_temp = thermal.get("package_temp_celsius")
    con.kv("package temp", "%.1f C" % pkg_temp if pkg_temp and pkg_temp > -100 else "N/A", indent=2)
    valid_temps = [t for t in thermal.get("core_temps_celsius", []) if t > 10]
    if valid_temps:
        con.kv("core temps", ", ".join("%.1f C" % t for t in valid_temps), indent=2)
    heat_flux = run.get("ml_features", {}).get("heat_flux") if "ml_features" in run else None
    if heat_flux is not None:
        con.kv("heat flux", "%.2f C/s %s" % (heat_flux, _level(heat_flux, 3.0, 1.5)), indent=2)
        if heat_flux > 3.0:
            con.line("rapid heating: thermal event risk", indent=3)
        elif heat_flux < 0:
            con.line("system cooling down", indent=3)

    during = derived.get("thermal_during_experiment", 0)
    now_active = derived.get("thermal_now_active", 0)
    since_boot = derived.get("thermal_since_boot", 0)
    # Experiment validity: no throttling during the experiment and none now.
    experiment_valid = during == 0 and now_active == 0
    con.line("experiment timeline", indent=1)
    con.kv("start", derived.get("exp_start_time", "N/A"), indent=2)
    con.kv("end", derived.get("exp_end_time", "N/A"), indent=2)
    con.line("throttling", indent=1)
    con.kv("since boot", _yes_no(since_boot), indent=2)
    con.kv("during experiment", _yes_no(during), indent=2)
    con.kv("active at end", _yes_no(now_active), indent=2)
    con.kv("experiment valid", con.style(_yes_no(experiment_valid), "green" if experiment_valid else "red"), indent=1)


def _show_system_state(con, run):
    """System state metrics M3-1 to M3-6."""
    if "ml_features" not in run:
        return
    ml = run["ml_features"]
    con.line("system state", indent=1)
    con.kv("cpu governor", ml.get("governor", "unknown"), indent=2)
    con.kv("turbo boost", "ENABLED" if ml.get("turbo_enabled", 0) else "DISABLED", indent=2)
    intr_rate = ml.get("interrupt_rate", 0)
    baseline_intr = ml.get("baseline_interrupt_rate", 2000)
    ratio = intr_rate / baseline_intr if baseline_intr > 0 else 1.0
    con.kv("interrupt rate", "%.0f/s %s" % (intr_rate, _level(ratio, 2.0, 1.2)), indent=2)
    start_temp = ml.get("start_temp_c", 0)
    max_temp = ml.get("max_temp_c", 0)
    if start_temp > 0 and max_temp > 0:
        con.kv("temperature", "%.1f C to %.1f C (%+.1f C)" % (start_temp, max_temp, max_temp - start_temp), indent=2)
    if ml.get("is_cold_start", 0):
        con.kv("cold start", "YES (first run)", indent=2)
    bg_cpu = ml.get("background_cpu_percent", 0)
    proc_count = ml.get("process_count", 0)
    if bg_cpu > 0 or proc_count > 0:
        con.kv("background cpu", "%.1f%%" % bg_cpu, indent=2)
        con.kv("running processes", proc_count, indent=2)
    rss = ml.get("rss_memory_mb", 0)
    vms = ml.get("vms_memory_mb", 0)
    if rss > 0 or vms > 0:
        con.kv("process memory", "RSS %.1f MB, VMS %.1f MB" % (rss, vms), indent=2)


def _show_power_sched(con, derived):
    """Power states (Req 1.7, 1.41, 1.8, 1.4) and scheduler metrics (Req 1.23, 1.36)."""
    power = derived.get("power_states", {})
    logger.debug("power_states keys %s, scheduler keys %s", list(power.keys()), list(derived.get("scheduler", {}).keys()))
    con.line("power states (Req 1.7, 1.41, 1.8, 1.4)", indent=1)
    states = ["%s: %.1f%%" % (k, v) for k, v in power.get("c_state_residencies", {}).items() if v > 0]
    if states:
        con.kv("c state residency", ", ".join(states), indent=2)
        con.line("per core average; values can sum above 100% as each core reports independently", indent=3)
    con.kv("cpu frequency", "%.0f MHz" % power.get("frequency_mhz", 0), indent=2)
    if power.get("gpu_frequency_mhz", 0) > 0:
        con.kv("gpu frequency", "%.0f MHz" % power["gpu_frequency_mhz"], indent=2)
    if power.get("gpu_rc6_percent", 0) > 0:
        con.kv("gpu rc6", "%.1f%%" % power["gpu_rc6_percent"], indent=2)

    scheduler = derived.get("scheduler", {})
    con.line("scheduler", indent=1)
    con.kv("voluntary ctx sw", "{:,}".format(scheduler.get("context_switches_voluntary", 0)), indent=2)
    con.kv("involuntary ctx sw", "{:,}".format(scheduler.get("context_switches_involuntary", 0)), indent=2)
    con.kv("thread migrations", "{:,}".format(scheduler.get("thread_migrations", 0)), indent=2)
    con.kv("run queue length", "%.2f" % scheduler.get("run_queue_length", 0), indent=2)
    con.kv("kernel time", "%.2f ms" % scheduler.get("kernel_time_ms", 0), indent=2)
    con.kv("user time", "%.2f ms" % scheduler.get("user_time_ms", 0), indent=2)


def _show_msr(con, derived):
    """MSR metrics: ring bus, wake up latency, throttle, C state times, TSC."""
    msr_data = derived.get("msr", {})
    if not msr_data:
        return
    if isinstance(msr_data, dict):
        logger.debug("msr content: %.200s", str(msr_data))
    con.line("MSR metrics", indent=1)
    ring_bus = msr_data.get("ring_bus", {})
    if ring_bus and ring_bus.get("current_mhz"):
        con.kv("ring bus frequency", "%.1f MHz" % ring_bus["current_mhz"], indent=2)
    if msr_data.get("wakeup_latency_us"):
        con.kv("wake up latency", "%.2f us" % msr_data["wakeup_latency_us"], indent=2)
    throttle = msr_data.get("thermal_throttle")
    if throttle is not None:
        con.kv("thermal throttle flag", "%s (%s)" % (throttle, "DETECTED" if throttle else "NOT DETECTED"), indent=2)
    for state, state_data in msr_data.get("c_states", {}).items():
        seconds = state_data.get("seconds", 0)
        if seconds <= 0:
            continue
        if seconds < 60:
            time_str = "%.2f seconds" % seconds
        elif seconds < 3600:
            time_str = "%.2f minutes" % (seconds / 60)
        else:
            time_str = "%.2f hours" % (seconds / 3600)
        con.kv("%s since boot" % state.upper(), time_str, indent=2)
    if msr_data.get("tsc_frequency_hz", 0):
        con.kv("tsc frequency", "%.0f MHz" % (msr_data["tsc_frequency_hz"] / 1e6), indent=2)


def display_hardware(runs, label):
    """Display ALL hardware parameters from DerivedEnergyMeasurement (Layer 3)."""
    con = get_console()
    for idx, run in enumerate(runs):
        derived = run["layer3_derived"]
        con.line("")
        con.line(con.style("%s run %d" % (label, idx + 1), "bold"))
        _show_energy_perf(con, derived)
        _show_thermal(con, run, derived)
        _show_system_state(con, run)
        _show_power_sched(con, derived)
        _show_msr(con, derived)


def display_ipc_analysis(all_linear, all_agentic):
    """
    Display IPC efficiency analysis comparing linear and agentic runs.

    Args:
        all_linear: List of linear run results
        all_agentic: List of agentic run results
    """
    if not all_linear or not all_agentic:
        return
    linear_ipcs = [r["layer3_derived"].get("performance", {}).get("ipc", 0) for r in all_linear]
    agentic_ipcs = [r["layer3_derived"].get("performance", {}).get("ipc", 0) for r in all_agentic]
    linear_ipcs = [i for i in linear_ipcs if i > 0]
    agentic_ipcs = [i for i in agentic_ipcs if i > 0]
    if not linear_ipcs or not agentic_ipcs:
        return
    avg_linear_ipc = sum(linear_ipcs) / len(linear_ipcs)
    avg_agentic_ipc = sum(agentic_ipcs) / len(agentic_ipcs)
    ipc_ratio = avg_agentic_ipc / avg_linear_ipc if avg_linear_ipc > 0 else 0

    con = get_console()
    con.section("IPC efficiency analysis")
    con.kv("linear ipc", "%.2f" % avg_linear_ipc)
    con.kv("agentic ipc", "%.2f" % avg_agentic_ipc)
    con.kv("efficiency ratio", con.style("%.2fx" % ipc_ratio, "cyan"))
    if ipc_ratio > 1:
        con.line("agentic workflow keeps the CPU %.1f%% busier: higher instruction density during orchestration" % ((ipc_ratio - 1) * 100), indent=1)
    elif ipc_ratio < 1:
        con.line("agentic workflow is %.1f%% less CPU efficient: more pipeline stalls or cache misses" % ((1 - ipc_ratio) * 100), indent=1)
    else:
        con.line("no IPC difference between workflows", indent=1)


def display_sustainability_header(all_linear):
    """Display grid information and sources."""
    con = get_console()
    con.section("sustainability impact")
    if not all_linear or not all_linear[0].get("sustainability"):
        return
    sus = all_linear[0]["sustainability"]
    con.kv("grid region", all_linear[0].get("country_code", "US"))
    if sus and "carbon" in sus:
        c = sus["carbon"]
        con.kv("carbon intensity", c.get("source", "Unknown"))
        con.kv("factor", "%.1f g/kWh" % c.get("grams_per_kwh", 0), indent=2)
        con.kv("uncertainty", "±%s%% [Req 2.16]" % c.get("uncertainty_percent", 0), indent=2)
    if sus and "water" in sus:
        con.kv("water intensity", sus["water"].get("source", "Unknown"))
    if sus and "methane" in sus:
        con.kv("methane leakage", sus["methane"].get("source", "Unknown"))


def _ratio(a, b):
    """Agentic over linear ratio; nan when linear is zero (as before)."""
    return (a / b) if b else float("nan")


def display_workflow_comparison(linear_stats, agentic_stats):
    """Display workflow comparison table."""
    if not linear_stats or not agentic_stats:
        return
    con = get_console()
    con.section("workflow comparison")
    con.line(con.style(f"{'Metric':<20} {'LINEAR':>15} {'AGENTIC':>15} {'RATIO':>10}", "bold"), indent=1)
    con.line("-" * 60, indent=1)
    rows = [
        ("Energy (J)", linear_stats["energy"], agentic_stats["energy"], 1, "energy"),
        ("Carbon (mg)", linear_stats["carbon"], agentic_stats["carbon"], 1000, "carbon"),
        ("Water (ul)", linear_stats["water"], agentic_stats["water"], 1000, "water"),
        ("Methane (mg)", linear_stats["methane"], agentic_stats["methane"], 1000, "methane"),
    ]
    for name, lin, agt, scale, _key in rows:
        ratio = _ratio(agt, lin)
        con.line(f"{name:<20} {lin * scale:>15.6f} {agt * scale:>15.6f} " + con.style(f"{ratio:>10.2f}x", "cyan"), indent=1)
    con.line("-" * 60, indent=1)


def display_pair_hardware(linear_results, agentic_results, title=None):
    """Display hardware parameters for each pair."""
    con = get_console()
    if title:
        con.section(title)
    for i in range(len(linear_results)):
        con.line("")
        con.line(con.style("pair %d: linear and agentic" % (i + 1), "bold"))
        display_hardware([linear_results[i]], "LINEAR run %d" % (i + 1))
        if i < len(agentic_results):
            display_hardware([agentic_results[i]], "AGENTIC run %d" % (i + 1))
    display_ipc_analysis(linear_results, agentic_results)
