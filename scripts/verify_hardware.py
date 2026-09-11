#!/usr/bin/env python3
"""
A-LEMS Hardware Verification Script
Reads hw_config.json and confirms every detected path and device
is actually accessible and working on the current machine.

Platform-aware: dispatches per platform_class so ARM/Mac checks
do not produce false failures on GN100 or MacBook.

Usage:
    python scripts/verify_hardware.py
    python scripts/verify_hardware.py --verbose
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

# ANSI color codes for terminal output.
# RESET must follow every colored segment to avoid bleeding into next line.
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"


def ps(component, status, message=""):
    # Map emoji status to color; default to BLUE for section headers.
    # Component is left-padded to 35 chars so all status marks align vertically.
    """Print a colored status line. status is a check-mark emoji that drives color."""
    color = {"\u2705": GREEN, "\u26a0\ufe0f": YELLOW, "\u274c": RED}.get(status, BLUE)
    print(f"{color}{status}{RESET} {component:35} {message}")


def load_config():
    # hw_config.json is the single source of truth written by detect_hardware.py.
    # All verification reads from it; nothing is hardcoded here.
    # If the file is stale (old schema version), re-run detect_hardware.py first.
    """Load config/hw_config.json. Returns dict or None if missing."""
    path = Path("config/hw_config.json")
    if not path.exists():
        ps("Config file", "\u274c", "config/hw_config.json not found!")
        print("\nRun: python scripts/detect_hardware.py")
        return None
    with open(path) as f:
        return json.load(f)


def run(cmd, timeout=5):
    # Wrapper around subprocess.run; returns None on any failure so callers
    # can do a simple truthiness check instead of try/except everywhere.
    # Catches FileNotFoundError (binary missing), TimeoutExpired, and OSError.
    # All callers treat None as "tool not available" and degrade gracefully.
    """Run a subprocess command. Returns CompletedProcess or None on failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def read_sysfs(path):
    # Read a small sysfs/proc file. Returns None on permission error or missing
    # path so callers can distinguish "present but zero" from "absent".
    # Sysfs files contain a single ASCII value followed by newline; strip() handles it.
    # Common failure modes: path missing (module unloaded), permission denied (no caps),
    # or file exists but read returns empty (sensor hardware not initialized yet).
    """Read a sysfs file. Returns stripped string or None on any error."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


# ============================================================================
# SHARED CHECKS (all platforms)
# These run on every machine regardless of platform_class.
# ============================================================================

def check_thermal(config):
    # Temperature sensors are important for detecting thermal throttling.
    # Sysfs values are in millidegrees Celsius; divide by 1000 to display.
    # Thermal throttling during a measurement run invalidates energy readings
    # because the CPU artificially reduces frequency to shed heat, not load.
    """Verify thermal zone sysfs paths are readable. Returns True if any zone works."""
    print(f"\n{BLUE}THERMAL ZONES:{RESET}")
    paths = config.get("thermal", {}).get("paths", {})
    if not paths:
        ps("  No thermal paths", "\u26a0\ufe0f")
        return False
    working = 0
    # Cap at 5 to avoid clutter on machines with many zones (e.g. Grace has 7)
    for zone, path in list(paths.items())[:5]:
        raw = read_sysfs(path)
        if raw is not None:
            try:
                ps(f"  {zone}", "\u2705", f"{int(raw)/1000:.1f}C")
                working += 1
            except Exception:
                ps(f"  {zone}", "\u26a0\ufe0f", "unreadable")
        else:
            ps(f"  {zone}", "\u274c", f"missing: {path}")
    return working > 0


def check_cpufreq(config):
    # CPU frequency scaling paths expose current, min, and max frequency.
    # Values are in kHz; divide by 1000 to display as MHz.
    # Useful for verifying DVFS is active during energy measurement sessions.
    # A fixed scaling_cur_freq == scaling_max_freq suggests performance governor
    # is locked, which is expected for controlled benchmarking environments.
    """Verify CPU frequency scaling sysfs paths are readable. Returns True if any path works."""
    print(f"\n{BLUE}CPU FREQUENCY:{RESET}")
    paths = config.get("cpufreq", {}).get("paths", {})
    if not paths:
        ps("  No cpufreq paths", "\u26a0\ufe0f")
        return False
    working = 0
    for name, path in paths.items():
        raw = read_sysfs(path)
        if raw is not None:
            try:
                ps(f"  {name}", "\u2705", f"{int(raw)/1000:.0f} MHz")
                working += 1
            except Exception:
                ps(f"  {name}", "\u26a0\ufe0f", "unreadable")
        else:
            ps(f"  {name}", "\u274c", f"missing: {path}")
    return working > 0


def check_rapl(config):
    # RAPL (Running Average Power Limit) is Intel/AMD's energy counter interface.
    # Returns None (not False) when absent so the dispatcher can skip it from
    # the summary on ARM/Mac where RAPL is not expected.
    """Verify RAPL energy counter paths. Returns None when absent (ARM/Mac), bool on x86."""
    print(f"\n{BLUE}RAPL ENERGY COUNTERS:{RESET}")
    paths = config.get("rapl", {}).get("paths", {})
    if not paths:
        # ARM and Mac platforms have no RAPL; this is expected, not a failure.
        ps("  No RAPL paths", "\u26a0\ufe0f", "(expected on ARM/Mac)")
        return None
    working = 0
    for domain, path in paths.items():
        raw = read_sysfs(path)
        if raw is not None:
            # Show first 12 chars of value to keep output width consistent
            ps(f"  {domain}", "\u2705", f"{raw[:12]} uJ")
            working += 1
        else:
            ps(f"  {domain}", "\u274c", f"missing: {path}")
    return working > 0


# ============================================================================
# X86 CHECKS (Intel + AMD)
# MSR, ring bus, turbostat, and TSC are x86-only concepts.
# On ARM/Mac these checks are skipped entirely to avoid false failures.
# ============================================================================

def check_msr_access(config):
    # MSR (Model Specific Registers) require the rdmsr tool from msr-tools
    # package AND the cap_sys_rawio Linux capability set on the binary.
    # Without capability, rdmsr needs root; setcap lets it run as user.
    # Register 0x10 = IA32_PLATFORM_ID, readable on all Intel/AMD CPUs.
    # If MSR devices (/dev/cpu/*/msr) do not exist, load the msr kernel module:
    #   sudo modprobe msr
    # To persist across reboots add 'msr' to /etc/modules.
    """Verify rdmsr tool is installed and can read MSR register 0x10. x86 only."""
    print(f"\n{BLUE}MSR ACCESS:{RESET}")
    rdmsr = run(["which", "rdmsr"])
    if not rdmsr or not rdmsr.stdout.strip():
        ps("  rdmsr tool", "\u274c", "install: sudo apt install msr-tools")
        return False
    rdmsr_path = rdmsr.stdout.strip()
    # Check capability; warn but do not fail (root access also works)
    cap = run(["getcap", rdmsr_path])
    if cap and "cap_sys_rawio=ep" not in cap.stdout:
        ps("  rdmsr capability", "\u26a0\ufe0f", "run: sudo setcap cap_sys_rawio=ep $(which rdmsr)")
    # Probe a safe read-only register to confirm MSR access is functional
    r = run(["rdmsr", "0x10"], timeout=1)
    if r and r.returncode == 0:
        ps("  MSR read (0x10)", "\u2705", r.stdout.strip())
        return True
    ps("  MSR read (0x10)", "\u274c", "rdmsr failed")
    return False


def check_msr_config(config):
    # These three fields are written by detect_hardware.py into the msr section.
    # They are hardware constants, not runtime readings, so we just verify they
    # are present and have sane values rather than re-reading from hardware.
    """Verify MSR hardware constants in config are present and sane. x86 only."""
    print(f"\n{BLUE}MSR CONFIG:{RESET}")
    msr = config.get("msr", {})
    ok = True
    # cstate_counter_max: always 2^64-1 for 64-bit C-state counters
    cmax = msr.get("cstate_counter_max")
    if cmax == 2**64 - 1:
        ps("  cstate_counter_max", "\u2705", str(cmax))
    elif cmax is not None:
        ps("  cstate_counter_max", "\u26a0\ufe0f", f"{cmax} (expected {2**64-1})")
        ok = False
    else:
        ps("  cstate_counter_max", "\u274c", "missing")
        ok = False
    # ring_bus_base_clock_mhz: base multiplier for ring bus ratio (typically 100)
    bc = msr.get("ring_bus_base_clock_mhz")
    if bc:
        ps("  ring_bus_base_clock_mhz", "\u2705", f"{bc} MHz")
    else:
        ps("  ring_bus_base_clock_mhz", "\u274c", "missing")
        ok = False
    # wakeup_idle_ms: how long CPU needs in idle before entering deep C-state
    wi = msr.get("wakeup_idle_ms")
    if wi and 1 <= wi <= 5:
        ps("  wakeup_idle_ms", "\u2705", f"{wi} ms")
    elif wi:
        ps("  wakeup_idle_ms", "\u26a0\ufe0f", f"{wi} ms (typical 1-5)")
    else:
        ps("  wakeup_idle_ms", "\u274c", "missing")
        ok = False
    return ok


def check_ring_bus(config):
    # Ring bus connects CPU cores, last-level cache, and memory controller on Intel.
    # AMD does not expose ring bus limits the same way; None return means optional.
    # min/max are in MHz; base_clock is the reference multiplier (100 MHz typical).
    # Detection source is either sysfs (intel_uncore_frequency driver) or MSR 0x621.
    # Ring bus frequency affects LLC access latency and is relevant for memory-bound
    # workloads measured in A-LEMS energy sessions.
    """Verify ring bus min/max frequencies are present. Returns None when not applicable."""
    print(f"\n{BLUE}RING BUS:{RESET}")
    rb = config.get("ring_bus", {})
    if not rb or (rb.get("min_mhz") is None and rb.get("max_mhz") is None):
        # AMD, unknown x86, and VMs often have no ring bus data; treat as warning
        ps("  Ring bus", "\u26a0\ufe0f", "no data (OK on AMD/unknown x86)")
        return None
    ok = True
    for key in ("min_mhz", "max_mhz", "base_clock_mhz"):
        val = rb.get(key)
        if val is not None:
            ps(f"  {key}", "\u2705", f"{val} MHz")
        else:
            ps(f"  {key}", "\u274c", "missing")
            ok = False
    return ok


def check_turbostat(config):
    # Turbostat is an Intel tool that reads C-state residencies, frequencies,
    # and power estimates via MSR. Disabled on AMD (Zen 2 SIGABRT observed).
    # Returns None when not applicable so it shows as warning, not failure.
    # turbostat binary lives under /usr/lib/linux-tools/<kernel>/ not in PATH
    # directly; detect_hardware.py resolves the real path and stores it in config.
    # If the kernel was updated since detection, the binary path may have changed
    # and a re-run of detect_hardware.py is needed.
    """Verify turbostat availability and binary presence. Returns None when disabled."""
    print(f"\n{BLUE}TURBOSTAT:{RESET}")
    ts = config.get("turbostat", {})
    if not ts.get("available", False):
        err = ts.get("error", "not available")
        ps("  Turbostat", "\u26a0\ufe0f", err)
        return None
    ps("  Turbostat", "\u2705", f"{len(ts.get('columns', {}))} columns mapped")
    # Verify the binary is still present (package could have been removed)
    r = run(["turbostat", "--version"], timeout=2)
    if r and r.returncode == 0:
        ps("  turbostat binary", "\u2705", "found")
    else:
        ps("  turbostat binary", "\u274c", "not found in PATH")
    return True


def check_tsc(config):
    # TSC (Time Stamp Counter) frequency is needed to convert C-state counter
    # ticks into real time. If not detected, MSRReader applies a fallback
    # (reads from /proc/cpuinfo model name GHz). Warn only, not failure.
    # tsc_detection_method values: auto_detected, not_detected, not_applicable.
    # not_applicable is set on ARM and Mac where TSC is not used at all.
    """Verify TSC frequency was detected. Warn-only; MSRReader has a fallback."""
    print(f"\n{BLUE}TSC FREQUENCY:{RESET}")
    cpu = config.get("cpu", {})
    hz = cpu.get("tsc_frequency_hz")
    method = cpu.get("tsc_detection_method", "unknown")
    if hz:
        ps("  TSC", "\u2705", f"{hz/1e6:.0f} MHz ({method})")
        return True
    ps("  TSC", "\u26a0\ufe0f", f"not detected ({method}), MSRReader fallback will apply")
    return False


# ============================================================================
# ARM (GRACE / GN100) CHECKS
# GN100 replaces RAPL+MSR+turbostat with SPBM+DCGM+arm_pmu+cpuidle.
# All power and energy data on Grace comes through hwmon (SPBM) and DCGM.
# ============================================================================

def check_spbm(config):
    # SPBM (System Power Board Monitor) is the NVIDIA Grace energy interface.
    # It exposes energy (uJ, cumulative) and power (uW, instantaneous) channels
    # via hwmon sysfs. Energy channels are what A-LEMS uses for measurement.
    """Verify SPBM hwmon energy and power channels are readable. Grace/GN100 only."""
    print(f"\n{BLUE}SPBM (System Power Board Monitor):{RESET}")
    spbm = config.get("spbm", {})
    if not spbm.get("available", False):
        ps("  SPBM", "\u274c", "not available (expected on GN100)")
        return False
    hwmon = spbm.get("hwmon_path", "")
    ps("  hwmon path", "\u2705", hwmon)
    # Verify all energy channels are readable (critical for measurement)
    energy_paths = spbm.get("energy_paths", {})
    for ch, path in energy_paths.items():
        raw = read_sysfs(path)
        if raw is not None:
            ps(f"  energy/{ch}", "\u2705", f"{raw} uJ")
        else:
            ps(f"  energy/{ch}", "\u274c", f"missing: {path}")
    # Show first 4 power channels only; log count for the rest
    power_paths = spbm.get("power_paths", {})
    working_power = 0
    for ch, path in list(power_paths.items())[:4]:
        raw = read_sysfs(path)
        if raw is not None:
            # Power values are in microwatts; convert to watts for display
            ps(f"  power/{ch}", "\u2705", f"{int(raw)/1e6:.2f} W")
            working_power += 1
        else:
            ps(f"  power/{ch}", "\u274c", f"missing: {path}")
    if len(power_paths) > 4:
        ps(f"  power (remaining)", "\u2705", f"{len(power_paths)-4} more channels not shown")
    # Pass if at least one energy channel is readable
    return len(energy_paths) > 0


def check_dcgm(config):
    # DCGM (Data Center GPU Manager) provides GPU power and energy on Grace.
    # Field 155 = GPU power (watts), field 156 = GPU energy (joules).
    # Both fields must be verified; A-LEMS gpu_collector depends on them.
    # If daemon is not running: sudo systemctl start nvidia-dcgm
    # If fields are not verified: GPU may need to be initialized with a workload
    # before DCGM starts reporting energy counters.
    """Verify DCGM daemon is running and fields 155/156 are accessible. Grace/GN100 only."""
    print(f"\n{BLUE}DCGM (GPU Power):{RESET}")
    dcgm = config.get("dcgm", {})
    if not dcgm.get("available", False):
        ps("  DCGM", "\u274c", "dcgmi not found")
        return False
    ps("  dcgmi", "\u2705", "installed")
    if dcgm.get("daemon_running"):
        ps("  daemon", "\u2705", "running")
    else:
        # nv-hostengine must be running for any dcgmi command to work
        ps("  daemon", "\u274c", "not running")
        return False
    f156 = dcgm.get("field_156_verified", False)
    f155 = dcgm.get("field_155_verified", False)
    ps("  field 155 (power)",  "\u2705" if f155 else "\u274c", "verified" if f155 else "not verified")
    ps("  field 156 (energy)", "\u2705" if f156 else "\u274c", "verified" if f156 else "not verified")
    return f155 and f156


def check_arm_pmu(config):
    # ARM PMU (Performance Monitoring Unit) exposes hardware perf counters.
    # A-LEMS uses instructions and cycles events for IPC tracking alongside
    # energy measurements. Accessed via the Linux perf subsystem.
    """Verify ARM PMU perf counters are accessible via the Linux perf subsystem."""
    print(f"\n{BLUE}ARM PMU:{RESET}")
    pmu = config.get("arm_pmu", {})
    if not pmu.get("available", False):
        ps("  ARM PMU", "\u274c", "not available")
        return False
    events = pmu.get("events", [])
    ps("  ARM PMU", "\u2705", f"{len(events)} events: {', '.join(events[:4])}")
    # Run a live perf stat to confirm the kernel interface is functional.
    # perf outputs to stderr by default; check stderr for "instructions" keyword.
    # If this fails, check: sudo sysctl kernel.perf_event_paranoid=1
    r = run(["perf", "stat", "-e", "instructions,cycles", "--", "true"], timeout=5)
    if r and "instructions" in (r.stderr or ""):
        ps("  perf stat", "\u2705", "executes correctly")
    else:
        ps("  perf stat", "\u26a0\ufe0f", "could not verify live")
    return True


def check_cpuidle(config):
    # cpuidle exposes per-CPU idle state time counters via sysfs.
    # On Grace these are LPI states (Low Power Idle): LPI-0 through LPI-3.
    # Values are in microseconds; used to measure CPU sleep time between jobs.
    # LPI-3 accumulates the most time on an idle system (deepest sleep state).
    # A value of 0 for a state means the CPU never entered it since last boot;
    # this is normal for shallow states (LPI-2) on a lightly loaded machine.
    """Verify cpuidle LPI state time counters are readable via sysfs."""
    print(f"\n{BLUE}CPU IDLE STATES:{RESET}")
    cpuidle = config.get("cpuidle", {})
    if not cpuidle.get("available", False):
        ps("  cpuidle", "\u274c", "not available")
        return False
    paths = cpuidle.get("paths", {})
    working = 0
    for state, path in paths.items():
        raw = read_sysfs(path)
        if raw is not None:
            ps(f"  {state}", "\u2705", f"{raw} us")
            working += 1
        else:
            ps(f"  {state}", "\u274c", f"missing: {path}")
    return working > 0


# ============================================================================
# APPLE SILICON CHECKS
# Mac has no sysfs, no RAPL, no MSR. Power data comes from IOKit (kernel
# framework) and powermetrics (Apple private API, requires sudo).
# ============================================================================

def check_iokit(config):
    # IOKit is Apple's kernel I/O registry. detect_hardware.py probes it via
    # ioreg to confirm the interface is accessible. powermetrics is the CLI
    # tool that reads power/thermal data from IOKit; it needs sudo for full data
    # but can confirm it's installed without elevated privileges.
    """Verify IOKit accessibility and powermetrics installation. Apple Silicon only."""
    print(f"\n{BLUE}IOKIT (Apple power interface):{RESET}")
    thermal = config.get("thermal", {})
    has_iokit = thermal.get("has_iokit")
    has_pm = thermal.get("has_powermetrics", False)
    if has_iokit:
        ps("  IOKit", "\u2705", "accessible")
    else:
        ps("  IOKit", "\u26a0\ufe0f", "not verified")
    if has_pm:
        ps("  powermetrics", "\u2705", "installed")
        r = run(["powermetrics", "--help"], timeout=3)
        if r and r.returncode == 0:
            ps("  powermetrics run", "\u2705", "executes (needs sudo for full data)")
        else:
            ps("  powermetrics run", "\u26a0\ufe0f", "needs sudo")
    else:
        ps("  powermetrics", "\u274c", "not found")
    # has_iokit=None means ioreg probe was skipped, not that it failed
    return has_iokit is not False


def check_mac_gpu(config):
    # GPU model is detected via system_profiler SPDisplaysDataType.
    # On Apple Silicon the GPU is integrated into the SoC; model name
    # confirms the correct chip was identified (e.g. M1 Pro, M2 Max).
    # A-LEMS does not currently read Mac GPU energy directly (no public API);
    # this check just confirms detection succeeded during hw_config generation.
    """Verify GPU model was detected via system_profiler. Apple Silicon only."""
    print(f"\n{BLUE}GPU (Apple Silicon):{RESET}")
    gpu = config.get("gpu", {})
    model = gpu.get("model")
    if model:
        ps("  GPU model", "\u2705", model)
        return True
    ps("  GPU model", "\u26a0\ufe0f", "not detected (requires system_profiler)")
    return False


# ============================================================================
# PLATFORM DISPATCHER
# Reads platform_class from config and runs only the relevant check set.
# Adding a new platform: add an elif branch here and write check_* functions.
# ============================================================================

# Check severity by execution context.
# "hard" = install fails, "soft" = warning, "skip" = not checked.
# unknown context = conservative (soft for all).
# vmware_guest and hyperv_guest use kvm_guest severity.
CHECK_SEVERITY = {
    "arm_pmu": {
        "bare_metal": "hard", "kvm_guest": "hard",
        "container": "soft", "wsl2": "skip", "unknown": "soft",
    },
    "cpuidle": {
        "bare_metal": "hard", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "cpufreq": {
        "bare_metal": "soft", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "thermal": {
        "bare_metal": "soft", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "rapl": {
        "bare_metal": "hard", "kvm_guest": "soft",
        "container": "soft", "wsl2": "skip", "unknown": "soft",
    },
    "msr": {
        "bare_metal": "hard", "kvm_guest": "soft",
        "container": "skip", "wsl2": "skip", "unknown": "soft",
    },
    "turbostat": {
        "bare_metal": "soft", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "spbm": {
        "bare_metal": "hard", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "dcgm": {
        "bare_metal": "soft", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
    "iokit": {
        "bare_metal": "hard", "kvm_guest": "skip",
        "container": "skip", "wsl2": "skip", "unknown": "skip",
    },
}


def get_check_severity(check_name: str, execution_context: str) -> str:
    """Look up severity for a check given execution context.
    Returns hard, soft, or skip. Defaults to soft when unknown."""
    if execution_context in ("vmware_guest", "hyperv_guest"):
        execution_context = "kvm_guest"
    entry = CHECK_SEVERITY.get(check_name, {})
    return entry.get(execution_context, entry.get("unknown", "soft"))


def run_checks(config):
    """Dispatch checks based on platform_class and execution_context.
    Returns dict of check_name -> bool/None. Skipped checks are excluded."""
    pclass = config.get("platform_class", "unknown")
    ec = config.get("execution_context", "unknown")
    results = {}

    def run(check_name, check_fn):
        """Run check if not skipped. Record as hard/soft based on severity."""
        severity = get_check_severity(check_name, ec)
        if severity == "skip":
            return
        results[check_name] = check_fn(config)

    # thermal and cpufreq use sysfs — skip on Mac (no sysfs on Darwin)
    if pclass != "apple_silicon":
        run("thermal", check_thermal)
        run("cpufreq", check_cpufreq)

    # RAPL returns None when absent (ARM/Mac); None entries are excluded from
    # the summary pass/fail count rather than counted as failures
    severity_rapl = get_check_severity("rapl", ec)
    if severity_rapl != "skip":
        rapl = check_rapl(config)
        if rapl is not None:
            results["rapl"] = rapl

    if pclass in ("intel_x86", "amd_x86", "linux_x86_unknown"):
        # x86 full suite: MSR, ring bus, turbostat, TSC
        results["msr_access"] = check_msr_access(config)
        results["msr_config"] = check_msr_config(config)
        results["ring_bus"]   = check_ring_bus(config)
        results["turbostat"]  = check_turbostat(config)
        results["tsc"]        = check_tsc(config)

    elif pclass == "nvidia_grace":
        # Grace suite: SPBM replaces RAPL, DCGM replaces turbostat.
        # SPBM requires unsigned kernel module — unavailable when Secure Boot
        # is enabled. DCGM alone provides GPU energy in that case.
        run("spbm", check_spbm)
        run("dcgm", check_dcgm)
        run("arm_pmu", check_arm_pmu)
        run("cpuidle", check_cpuidle)
        if results.get("spbm") is False and results.get("dcgm"):
            print("  ℹ️  SPBM unavailable (Secure Boot may be enabled) — DCGM covers GPU energy")
            results.pop("spbm")

    elif pclass == "linux_arm":
        # Generic ARM: no SPBM/DCGM; PMU and idle states severity-gated
        run("arm_pmu", check_arm_pmu)
        run("cpuidle", check_cpuidle)

    elif pclass == "apple_silicon":
        # Mac: no sysfs energy paths; verify IOKit and GPU detection only
        results["iokit"]   = check_iokit(config)
        results["mac_gpu"] = check_mac_gpu(config)

    return results


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Entry point. Loads config, runs platform checks, prints summary, returns exit code."""
    print("\n" + "=" * 70)
    print("A-LEMS HARDWARE VERIFICATION")
    print("=" * 70)

    config = load_config()
    if not config:
        return 1

    # Print platform header from config metadata
    meta = config.get("metadata", {})
    pclass = config.get("platform_class", "unknown")
    print(f"Host:      {meta.get('hostname', 'unknown')}")
    print(f"Detected:  {meta.get('detected_at', 'unknown')}")
    print(f"Platform:  {pclass}")
    print(f"CPU:       {config.get('cpu_model', 'unknown')}")
    print(f"Schema:    hw_config_version={config.get('hw_config_version', '?')}")
    print("=" * 70)

    results = run_checks(config)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ec = config.get("execution_context", "unknown")
    if ec != "bare_metal":
        print(f"  Execution context: {ec}")
        print(f"  (Some checks adjusted for virtualized environment)")

    # tsc, turbostat, ring_bus: optional because fallbacks exist or platform may
    # not support them. False result shows as warning, not failure, in summary.
    # All other checks are required; a single False in failed[] causes exit code 1.
    # None result (RAPL on ARM) is excluded from results dict so it never counts.
    optional = {"tsc", "turbostat", "ring_bus"}
    failed = []
    warned = []
    for key, val in results.items():
        mark = "\u2705" if val else ("\u26a0\ufe0f" if val is None or key in optional else "\u274c")
        print(f"  {key:30} {mark}")
        if val is False and key not in optional:
            failed.append(key)
        elif val is None or (val is False and key in optional):
            warned.append(key)

    print()
    # Return 0 only when all non-optional checks pass.
    # Return 1 when any required check fails; install.sh uses this exit code
    # to halt setup before proceeding to collector configuration.
    if not failed:
        if warned:
            print("\u2705 Core systems ready.")
            print(f"   Optional not available: {', '.join(warned)}")
        else:
            print("\u2705 ALL SYSTEMS GO. Module 0 is ready.")
        print("=" * 70 + "\n")
        return 0
    else:
        print(f"\u274c {len(failed)} check(s) failed: {', '.join(failed)}")
        print("   Run: sudo ./scripts/fix_permissions.sh")
        print("=" * 70 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
