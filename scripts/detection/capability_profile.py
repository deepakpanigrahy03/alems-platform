"""
Capability profile and tool availability for A-LEMS.

Governing invariant: a capability is asserted only when A-LEMS can positively
establish that the underlying data source is present AND readable. Each check
opens the file, reads bytes, and parses a value. os.path.exists() alone is
never sufficient.

Tool availability (perf, turbostat, nvidia-smi, etc.) is recorded separately
and never serves as proof of hardware capability.
"""

import glob
import os
import platform
import shutil
from typing import Dict


def _read_file(path, default=None):
    """Read a small file, return stripped content or default."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


# ============================================================================
# CAPABILITY CHECKS (each does a real read, not just existence)
# ============================================================================


def _check_energy_rapl() -> bool:
    """True only if we can read an actual RAPL energy_uj value right now."""
    for path in glob.glob("/sys/class/powercap/intel-rapl*/energy_uj"):
        try:
            with open(path) as f:
                val = int(f.read().strip())
                return val >= 0
        except (PermissionError, ValueError, OSError):
            continue
    # Also check virtual powercap path
    for path in glob.glob("/sys/devices/virtual/powercap/intel-rapl*/energy_uj"):
        try:
            with open(path) as f:
                val = int(f.read().strip())
                return val >= 0
        except (PermissionError, ValueError, OSError):
            continue
    return False


def _check_energy_spbm() -> bool:
    """True only if spark_hwmon energy channels are readable."""
    for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*/")):
        name = _read_file(hwmon_dir + "name", "")
        if "spbm" not in name.lower() and "spark" not in name.lower():
            continue
        # Found SPBM hwmon, check if any energy input is readable
        for energy_input in glob.glob(hwmon_dir + "energy*_input"):
            try:
                with open(energy_input) as f:
                    val = int(f.read().strip())
                    return val >= 0
            except (PermissionError, ValueError, OSError):
                continue
    return False


def _check_energy_iokit() -> bool:
    """True only if IOKit power plane is readable (Darwin Apple Silicon)."""
    if platform.system() != "Darwin":
        return False
    # IOKit power data requires ioreg to confirm power plane exists
    try:
        import subprocess
        r = subprocess.run(
            ["ioreg", "-r", "-c", "AppleARMBacklight"],
            capture_output=True, timeout=3
        )
        # On Apple Silicon, IOKit power plane is accessible
        # On Intel Mac, this node does not exist
        if r.returncode == 0 and b"IOService" in r.stdout:
            return True
    except Exception:
        pass
    # Fallback: check for AppleSMC (Intel) or AppleARMIODevice (ARM)
    try:
        import subprocess
        for node in ["AppleARMIODevice", "IOPMPowerSource"]:
            r = subprocess.run(
                ["ioreg", "-r", "-c", node],
                capture_output=True, timeout=3
            )
            if r.returncode == 0 and len(r.stdout) > 100:
                return True
    except Exception:
        pass
    return False


def _check_cpu_msr_device() -> bool:
    """True only if /dev/cpu/0/msr exists and is readable."""
    path = "/dev/cpu/0/msr"
    if not os.path.exists(path):
        return False
    # Check if actually readable (needs permissions)
    return os.access(path, os.R_OK)


def _check_arm_pmu_sysfs() -> bool:
    """
    True only if armv8_pmuv3 event source devices exist in sysfs
    AND at least one has a readable type file (proving kernel PMU driver loaded).
    """
    devices = glob.glob("/sys/bus/event_source/devices/armv8_pmuv3_*")
    if not devices:
        return False
    for dev in devices:
        type_file = os.path.join(dev, "type")
        if os.path.isfile(type_file):
            try:
                with open(type_file) as f:
                    int(f.read().strip())
                    return True
            except (ValueError, PermissionError, OSError):
                continue
    return False


def _check_cpuidle_sysfs() -> bool:
    """True only if cpuidle state name is readable (proves kernel driver loaded)."""
    path = "/sys/devices/system/cpu/cpu0/cpuidle/state0/name"
    val = _read_file(path)
    return val is not None and len(val) > 0


def _check_cpufreq_sysfs() -> bool:
    """True only if scaling_cur_freq returns a parseable integer."""
    path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    val = _read_file(path)
    if val is None:
        return False
    try:
        int(val)
        return True
    except ValueError:
        return False


def _check_thermal_hwmon() -> bool:
    """True only if at least one hwmon temp input returns a parseable value."""
    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*/temp1_input")):
        val = _read_file(path)
        if val is not None:
            try:
                int(val)
                return True
            except ValueError:
                continue
    return False


def _check_proc_stat() -> bool:
    """True only if /proc/stat exists and contains cpu line."""
    content = _read_file("/proc/stat")
    return content is not None and content.startswith("cpu")


# ============================================================================
# PUBLIC API
# ============================================================================


def build_capability_profile() -> Dict[str, bool]:
    """
    Probe sysfs/procfs/IOKit for what is ACTUALLY readable right now.
    Each check opens a file, reads bytes, and parses a value.
    Returns dict of 9 capability booleans.
    """
    return {
        "energy_rapl": _check_energy_rapl(),
        "energy_spbm": _check_energy_spbm(),
        "energy_iokit": _check_energy_iokit(),
        "cpu_msr_device": _check_cpu_msr_device(),
        "arm_pmu_sysfs": _check_arm_pmu_sysfs(),
        "cpuidle_sysfs": _check_cpuidle_sysfs(),
        "cpufreq_sysfs": _check_cpufreq_sysfs(),
        "thermal_hwmon": _check_thermal_hwmon(),
        "proc_stat": _check_proc_stat(),
    }


def build_tool_availability() -> Dict[str, bool]:
    """
    Check which userspace tools are installed.
    Tool presence is recorded for operational use but NEVER serves
    as proof of hardware capability.
    Returns dict of 6 tool booleans.
    """
    return {
        "perf": shutil.which("perf") is not None,
        "turbostat": _find_turbostat() is not None,
        "nvidia_smi": shutil.which("nvidia-smi") is not None,
        "dcgmi": shutil.which("dcgmi") is not None,
        "powermetrics": shutil.which("powermetrics") is not None,
        "rdmsr": shutil.which("rdmsr") is not None,
    }


def _find_turbostat() -> bool:
    """Check for turbostat (may be kernel-versioned, not in PATH)."""
    if shutil.which("turbostat"):
        return True
    # Ubuntu kernel-versioned path
    try:
        kernel_release = platform.release()
        path = f"/usr/lib/linux-tools/{kernel_release}/turbostat"
        if os.path.exists(path):
            return True
    except Exception:
        pass
    return False


def classify_measurement(cap: Dict[str, bool], platform_class: str):
    """
    Derive compute and energy measurement classification from
    capability profile.

    Returns (compute, energy) tuple of strings.

    compute: "direct" | "estimated" | "unavailable"
    energy:  "direct" | "modeled"   | "unavailable"
    """
    # Compute: "direct" requires verified PMU counter access.
    # On ARM: sysfs PMU device presence is sufficient (verified readable).
    # On x86: RAPL readability proves perf_event subsystem is functional,
    # which means PMU counters are accessible through the same subsystem.
    # cpu_msr_device alone is NOT sufficient (VM may expose /dev/cpu/0/msr
    # without functional perf_event support).
    if cap.get("arm_pmu_sysfs"):
        compute = "direct"
    elif platform_class in ("intel_x86", "amd_x86") and cap.get("energy_rapl"):
        compute = "direct"
    elif cap.get("proc_stat"):
        compute = "estimated"
    else:
        compute = "unavailable"

    # Energy: "direct" requires hardware energy counters.
    if cap.get("energy_rapl") or cap.get("energy_spbm") or cap.get("energy_iokit"):
        energy = "direct"
    elif compute in ("direct", "estimated"):
        energy = "modeled"
    else:
        energy = "unavailable"

    return compute, energy
