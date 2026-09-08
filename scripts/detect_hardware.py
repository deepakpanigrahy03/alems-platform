#!/usr/bin/env python3
"""
A-LEMS Hardware Detection Utility (Redesigned v2)
===================================================
Platform-first hardware detection. Probes only what the current platform
supports. Writes config/hw_config.json by default.

Architecture:
    PlatformDetector.for_current_platform()
        -> AppleSiliconDetector      (Darwin arm64)
        -> IntelLinuxDetector        (Linux x86_64 GenuineIntel)
        -> AMDLinuxDetector          (Linux x86_64 AuthenticAMD)
        -> GenericX86LinuxDetector   (Linux x86_64 unknown vendor)
        -> NVIDIAGraceDetector       (Linux aarch64, Grace SoC)
        -> GenericARMLinuxDetector    (Linux aarch64, non-Grace)
        -> RISCVLinuxDetector        (Linux riscv64)

Adding a platform requires changes only in this file, plus platform tests.
See GUIDE_ADDING_NEW_PLATFORM.md for the 4-step process.

Usage:
    # Default: writes config/hw_config.json, merges with existing
    python3 scripts/detect_hardware.py

    # With sudo for full MSR/turbostat access
    sudo python3 scripts/detect_hardware.py

    # View only, no file written
    python3 scripts/detect_hardware.py --stdout
"""

import argparse
import csv
import glob
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psutil

# ============================================================================
# CONSTANTS
# ============================================================================

# Single authoritative schema version for hw_config.json.
# Increment when the output contract changes (new required keys, removed keys,
# changed types). Consumers check this to detect stale configs.
HW_CONFIG_SCHEMA_VERSION = 3

# Legacy field name. Kept at 2 for consumers that check config_version.
# New code should check hw_config_version instead.
LEGACY_CONFIG_VERSION = 2

REQUIRED_TOP_LEVEL_KEYS = [
    "os_name",
    "cpu_architecture",
    "cpu_vendor",
    "platform_class",
    "hw_config_version",
]

# cpu_architecture is an extensible normalized architecture identifier.
# New platforms may introduce new values without changing consumers
# unless a consumer explicitly branches on architecture.
KNOWN_ARCHITECTURES = {"x86_64", "aarch64", "arm64", "riscv64"}

# Valid (cpu_vendor, platform_class) pairs. Semantic validation rejects
# mismatches. Extend both sets when adding a platform.
VALID_VENDOR_PLATFORM = {
    ("apple", "apple_silicon"),
    ("intel", "intel_x86"),
    ("amd", "amd_x86"),
    ("nvidia_grace", "nvidia_grace"),
    ("arm_unknown", "linux_arm"),
    ("riscv", "linux_riscv"),
    ("unknown", "linux_x86_unknown"),
    ("intel", "intel_mac"),
}


# ============================================================================
# UTILITY FUNCTIONS (platform-independent)
# ============================================================================


def _repo_root() -> Path:
    """Walk up from this script to find the repo root (contains config/)."""
    here = Path(__file__).resolve().parent
    candidate = here.parent
    if (candidate / "config").is_dir() or (candidate / "scripts").is_dir():
        return candidate
    return Path.cwd()


def _default_output_path() -> Path:
    return _repo_root() / "config" / "hw_config.json"


def _run(cmd, timeout=5):
    """Run a subprocess, return CompletedProcess or None on any failure."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return None


def _read_file(path, default=None):
    """Read a small sysfs/proc file. Returns stripped string or default."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def _detect_virtualization() -> Optional[str]:
    """Detect if running inside a VM. Returns virt type or None."""
    r = _run(["systemd-detect-virt"])
    if r and r.returncode == 0:
        virt = r.stdout.strip()
        if virt and virt != "none":
            return virt
    # Fallback: check DMI
    product = _read_file("/sys/class/dmi/id/product_name", "")
    manufacturer = _read_file("/sys/class/dmi/id/sys_vendor", "")
    vm_indicators = ["VirtualBox", "VMware", "QEMU", "KVM", "Hyper-V",
                     "Xen", "Parallels", "Bochs", "innotek"]
    for indicator in vm_indicators:
        if indicator.lower() in product.lower() or indicator.lower() in manufacturer.lower():
            return indicator.lower()
    return None


def generate_hardware_hash(hw_info: dict) -> str:
    hash_str = json.dumps(
        {
            "cpu_model": hw_info.get("cpu_model"),
            "cpu_cores": hw_info.get("cpu_cores"),
            "ram_gb": hw_info.get("ram_gb"),
            "gpu_model": hw_info.get("gpu_model"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(hash_str.encode()).hexdigest()[:16]


def create_backup(file_path: Path, max_backups: int = 5) -> Optional[Path]:
    """Timestamped backup of an existing config file."""
    if not file_path.exists():
        return None
    backup_dir = file_path.parent / "backups"
    if backup_dir.exists() and not os.access(backup_dir, os.W_OK):
        print(f"  Backup directory {backup_dir} is not writable.")
        print(f"  Fix: sudo chown -R $USER:$USER {backup_dir}")
        return None
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"  Cannot create backup directory {backup_dir}")
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{file_path.name}.{timestamp}.bak"
    try:
        shutil.copy2(file_path, backup_path)
    except PermissionError:
        print(f"  Cannot write backup to {backup_path}")
        return None
    try:
        backups = sorted(backup_dir.glob(f"{file_path.name}.*.bak"))
        for old in backups[:-max_backups]:
            old.unlink()
    except Exception:
        pass
    return backup_path


# ============================================================================
# THERMAL CLASSIFICATION (single implementation, vendor-aware)
# Fixes BUG-04: replaces 3x copy-pasted substring matching loops.
# ============================================================================


def discover_thermal_zones() -> Dict[str, list]:
    """Discover /sys/class/thermal/thermal_zone* sensors. Linux only."""
    base = "/sys/class/thermal"
    mapping = {}
    if not os.path.exists(base):
        return mapping
    for entry in os.listdir(base):
        if not entry.startswith("thermal_zone"):
            continue
        zone_path = os.path.join(base, entry)
        try:
            sensor_type = _read_file(os.path.join(zone_path, "type"))
            if not sensor_type:
                continue
            temp_file = os.path.join(zone_path, "temp")
            trip_file = os.path.join(zone_path, "trip_point_0_temp")
            throttle_temp = None
            raw = _read_file(trip_file)
            if raw is not None:
                try:
                    throttle_temp = int(raw) / 1000.0
                except ValueError:
                    pass
            if os.path.exists(temp_file):
                mapping.setdefault(sensor_type, []).append(
                    {"path": temp_file, "zone": entry, "throttle_temp": throttle_temp}
                )
        except Exception:
            continue
    return mapping


def discover_hwmon_thermal() -> Dict[str, str]:
    """Discover hwmon thermal sensors (k10temp, coretemp, etc.)."""
    result = {}
    for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*/")):
        name = _read_file(hwmon_dir + "name")
        if not name:
            continue
        temp_file = hwmon_dir + "temp1_input"
        if os.path.exists(temp_file):
            result[name] = temp_file
    return result


def classify_thermal_sensors(
    thermal_zones: Dict[str, list],
    hwmon_sensors: Dict[str, str],
    cpu_vendor: str,
) -> Dict[str, str]:
    """
    Classify discovered thermal sensors into semantic roles.
    Vendor-aware: branches on known cpu_vendor, not open-ended substring
    matching. Both discovery sources (thermal_zone AND hwmon) are received
    together so no discovery path is invisible to classification.

    Returns dict mapping role -> sensor_type/name.
    """
    sensors = {}

    # ---- Step 1: vendor-specific package temp detection ----
    if cpu_vendor == "intel":
        for stype in thermal_zones:
            if "x86_pkg_temp" in stype:
                sensors["cpu_package"] = stype
                break
        if "cpu_package" not in sensors and "coretemp" in hwmon_sensors:
            sensors["cpu_package"] = "coretemp"

    elif cpu_vendor == "amd":
        # AMD primary: k10temp hwmon (no thermal_zone on many AMD Ryzen)
        if "k10temp" in hwmon_sensors:
            sensors["cpu_package"] = "k10temp"
        else:
            for stype in thermal_zones:
                sl = stype.lower()
                if "cpu" in sl and "temp" in sl:
                    sensors["cpu_package"] = stype
                    break

    elif cpu_vendor == "nvidia_grace":
        for stype in thermal_zones:
            sl = stype.lower()
            if "cpu" in sl and "temp" in sl:
                sensors["cpu_package"] = stype
                break
        for stype in thermal_zones:
            if re.match(r"^[Ss][Ee][Nn]\d+$", stype):
                sensors[stype] = stype

    elif cpu_vendor in ("arm_unknown", "riscv", "unknown"):
        for stype in thermal_zones:
            sl = stype.lower()
            if "cpu" in sl and "temp" in sl:
                sensors["cpu_package"] = stype
                break

    # ---- Step 2: non-package sensors (all Linux platforms) ----
    for stype in thermal_zones:
        sl = stype.lower()
        if stype in sensors.values():
            continue
        if "tcpu" in sl and "cpu_alt" not in sensors:
            sensors["cpu_alt"] = stype
        elif ("wifi" in sl or "iwlwifi" in sl) and "wifi" not in sensors:
            sensors["wifi"] = stype
        elif ("acpi" in sl or "int3400" in sl) and "system" not in sensors:
            sensors["system"] = stype
        elif re.match(r"^[Ss][Ee][Nn]\d+$", stype):
            sensors[stype] = stype
        elif stype not in sensors.values():
            sensors[stype] = stype

    # ---- Step 3: loud warning if vendor known but package missing ----
    if cpu_vendor in ("intel", "amd") and "cpu_package" not in sensors:
        print(
            f"  WARNING: no CPU package temperature sensor found for "
            f"cpu_vendor={cpu_vendor}. Thermal data will be incomplete. "
            f"Check that the appropriate kernel module is loaded "
            f"(coretemp for Intel, k10temp for AMD)."
        )

    return sensors


# ============================================================================
# PLATFORM DETECTOR BASE CLASS
# ============================================================================


class PlatformDetector(ABC):
    """Base class for platform-specific hardware detection."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    @staticmethod
    def for_current_platform(verbose: bool = False) -> "PlatformDetector":
        """Factory: returns the correct detector for the current machine."""
        system = platform.system()
        machine = platform.machine()

        if system == "Darwin" and machine in ("arm64", "aarch64"):
            return AppleSiliconDetector(verbose)

        if system == "Linux":
            if machine == "x86_64":
                vendor = _detect_x86_vendor()
                if vendor == "amd":
                    return AMDLinuxDetector(verbose)
                if vendor == "intel":
                    return IntelLinuxDetector(verbose)
                # Unknown x86 vendor (Hygon, Centaur, VIA, Zhaoxin, VM, etc.)
                return GenericX86LinuxDetector(verbose)

            if machine == "aarch64":
                if _is_nvidia_grace():
                    return NVIDIAGraceDetector(verbose)
                return GenericARMLinuxDetector(verbose)

            if machine == "riscv64":
                return RISCVLinuxDetector(verbose)

            # Any other Linux arch (ppc64le, s390x, loongarch, future ISAs):
            # fall back to GenericARMLinuxDetector which uses only standard
            # Linux kernel interfaces (sysfs, cpufreq, thermal_zone, perf).
            # A-LEMS runs in observation-only mode on these platforms.
            print(f"  WARNING: Unknown Linux arch {machine}, using generic fallback")
            return GenericARMLinuxDetector(verbose)

        # macOS: Apple Silicon (arm64) or Intel (x86_64, rare but valid)
        if system == "Darwin":
            if machine in ("arm64", "aarch64"):
                return AppleSiliconDetector(verbose)
            # Intel Mac: no IOKit energy APIs, observation-only mode
            print(f"  WARNING: Intel Mac detected, energy measurement not available")
            return IntelMacDetector(verbose)

        raise RuntimeError(
            f"Unsupported platform: {system}/{machine}. "
            f"See GUIDE_ADDING_NEW_PLATFORM.md to add support."
        )

    @abstractmethod
    def detect(self) -> Dict[str, Any]:
        """Run all probes and return the complete hw_config dict."""

    def _metadata(self) -> dict:
        return {
            "detected_at": datetime.now().isoformat(),
            "hostname": platform.node(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }

    def _log(self, msg: str):
        if self.verbose:
            print(f"   {msg}")


# ============================================================================
# VENDOR / PLATFORM IDENTIFICATION (used by factory)
# ============================================================================


def _detect_x86_vendor() -> str:
    """Read vendor_id from /proc/cpuinfo on x86_64."""
    content = _read_file("/proc/cpuinfo")
    if content:
        for line in content.splitlines():
            if line.startswith("vendor_id"):
                val = line.split(":")[1].strip()
                if "AuthenticAMD" in val:
                    return "amd"
                if "GenuineIntel" in val:
                    return "intel"
                # Return the raw vendor string for logging
                return val.lower()
    return "unknown"


def _is_nvidia_grace() -> bool:
    """Detect any NVIDIA Grace-family SoC (GN100, DGX Spark, future variants).
    Uses hardware signals not product name strings so new Grace products
    are detected automatically without code changes."""
    # Signal 1: known product name substrings (fast path)
    product = _read_file("/sys/class/dmi/id/product_name", "")
    if any(tag in product for tag in ("GN100", "Grace", "Veriton", "DGX")):
        return True
    # Signal 2: GB10 SoC GPU present via nvidia-smi (covers all Grace variants)
    r = _run(["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"], timeout=5)
    if r and r.returncode == 0 and "GB10" in r.stdout:
        return True
    # Signal 3: NVIDIA CPU implementer code in /proc/cpuinfo
    content = _read_file("/proc/cpuinfo", "")
    for line in content.splitlines():
        if "CPU implementer" in line and "0x4e" in line:
            return True
    return False


# ============================================================================
# APPLE SILICON DETECTOR
# ============================================================================


class AppleSiliconDetector(PlatformDetector):

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: Apple Silicon (Darwin arm64)")
        cpu = self._detect_cpu()
        gpu = self._detect_gpu()
        ram_gb = psutil.virtual_memory().total // (1024**3)
        hw_model = self._sysctl("hw.model") or ""

        config = {
            "metadata": self._metadata(),
            "os_name": "Darwin",
            "cpu_architecture": platform.machine(),
            "cpu_vendor": "apple",
            "platform_class": "apple_silicon",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu,
            "cpu_model": cpu.get("model"),
            "cpu_cores": cpu.get("physical_cores"),
            "cpu_vendor_raw": "apple",
            "microcode_version": "N/A",
            "ram_gb": ram_gb,
            "gpu": gpu,
            "gpu_model": gpu.get("model"),
            "system_manufacturer": "Apple Inc.",
            "system_product": hw_model,
            "system_type": "laptop" if "Book" in hw_model else "desktop",
            "virtualization_type": None,
            "rapl": {"paths": {}, "available_domains": [], "has_dram": False},
            "thermal": {
                "paths": {}, "package_temp": None,
                "discovered_zones": {}, "sensors_to_monitor": {},
                "sampling_rate_hz": 1,
                "has_iokit": self._check_iokit(),
                "has_powermetrics": shutil.which("powermetrics") is not None,
            },
            "msr": {"devices": [], "count": 0},
            "cpufreq": {"paths": {}},
            "turbostat": {"available": False, "columns": {}, "error": "not applicable on Darwin"},
            "cpu_flags": {"has_avx2": False, "has_avx512": False, "has_vmx": False},
            "cpu_details": {"family": None, "model": None, "stepping": None},
            "ring_bus": {"min_mhz": None, "max_mhz": None, "base_clock_mhz": None, "sysfs_paths": {}},
            "system": {"manufacturer": "Apple Inc.", "product": hw_model, "type": None, "virtualization": None},
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_cpu(self) -> dict:
        brand = self._sysctl("machdep.cpu.brand_string") or "Apple Silicon"
        perf_s = self._sysctl("hw.perflevel0.physicalcpu") or ""
        eff_s = self._sysctl("hw.perflevel1.physicalcpu") or ""
        if perf_s.isdigit() and eff_s.isdigit():
            physical = int(perf_s) + int(eff_s)
        else:
            physical = int(self._sysctl("hw.physicalcpu") or "0") or (os.cpu_count() or 8)
        logical = int(self._sysctl("hw.logicalcpu") or "0") or physical
        self._log(f"CPU: {brand} ({physical}P cores)")
        return {
            "model": brand, "vendor": "apple", "microcode": "N/A",
            "physical_cores": physical, "logical_cores": logical,
            "cores_list": list(range(logical)),
            "tsc_frequency_hz": None, "tsc_detection_method": "not_applicable",
        }

    def _detect_gpu(self) -> dict:
        gpu = {"model": None, "driver": "iokit", "count": 0, "vendor": "apple"}
        r = _run(["system_profiler", "SPDisplaysDataType"])
        if r and r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    gpu["model"] = line.split(":", 1)[1].strip()
                    gpu["count"] = 1
                    self._log(f"GPU: {gpu['model']}")
        return gpu

    def _check_iokit(self) -> bool:
        r = _run(["ioreg", "-l", "-n", "AppleARMIODevice"], timeout=3)
        return r is not None and r.returncode == 0

    def _sysctl(self, key: str) -> Optional[str]:
        r = _run(["sysctl", "-n", key], timeout=3)
        if r and r.returncode == 0:
            return r.stdout.strip()
        return None


# ============================================================================
# INTEL MAC DETECTOR (Darwin x86_64, observation-only)
# ============================================================================


class IntelMacDetector(PlatformDetector):
    """Intel Mac (Darwin x86_64). No IOKit energy APIs available.
    Runs in observation-only mode. Rare in the A-LEMS fleet."""

    def detect(self) -> Dict[str, Any]:
        """Detect hardware on Intel Mac. Returns minimal config."""
        self._log("Platform: Intel Mac (Darwin x86_64)")
        ram_gb = psutil.virtual_memory().total // (1024**3)
        hw_model = self._sysctl("hw.model") or ""
        physical = int(self._sysctl("hw.physicalcpu") or "0") or (os.cpu_count() or 4)
        logical = int(self._sysctl("hw.logicalcpu") or "0") or physical

        config = {
            "metadata": self._metadata(),
            "os_name": "Darwin",
            "cpu_architecture": "x86_64",
            "cpu_vendor": "intel",
            "platform_class": "intel_mac",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": {
                "model": self._sysctl("machdep.cpu.brand_string") or "Intel Mac",
                "vendor": "intel", "microcode": "N/A",
                "physical_cores": physical, "logical_cores": logical,
                "cores_list": list(range(logical)),
                "tsc_frequency_hz": None, "tsc_detection_method": "not_applicable",
            },
            "cpu_model": self._sysctl("machdep.cpu.brand_string") or "Intel Mac",
            "cpu_cores": physical,
            "cpu_vendor_raw": "intel",
            "microcode_version": "N/A",
            "ram_gb": ram_gb,
            "system_manufacturer": "Apple Inc.",
            "system_product": hw_model,
            "system_type": "laptop" if "Book" in hw_model else "desktop",
            "virtualization_type": None,
            "rapl": {"paths": {}, "available_domains": [], "has_dram": False},
            "thermal": {"paths": {}, "package_temp": None,
                        "discovered_zones": {}, "sensors_to_monitor": {},
                        "sampling_rate_hz": 1, "has_iokit": False,
                        "has_powermetrics": shutil.which("powermetrics") is not None},
            "msr": {"devices": [], "count": 0},
            "cpufreq": {"paths": {}},
            "turbostat": {"available": False, "columns": {},
                          "error": "not applicable on Darwin"},
            "ring_bus": {"min_mhz": None, "max_mhz": None,
                         "base_clock_mhz": None, "sysfs_paths": {}},
            "cpu_flags": {"has_avx2": False, "has_avx512": False, "has_vmx": False},
            "cpu_details": {"family": None, "model": None, "stepping": None},
            "gpu": {"model": None, "driver": None, "count": 0, "vendor": "intel"},
            "gpu_model": None,
            "system": {"manufacturer": "Apple Inc.", "product": hw_model,
                       "type": None, "virtualization": None},
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _sysctl(self, key: str) -> Optional[str]:
        """Read a sysctl key. Returns None if unavailable."""
        r = _run(["sysctl", "-n", key], timeout=3)
        if r and r.returncode == 0:
            return r.stdout.strip()
        return None


# ============================================================================
# LINUX SHARED PROBES
# ============================================================================


class LinuxX86Mixin:
    """Shared probe methods for x86_64 Linux (Intel, AMD, unknown x86)."""

    def _detect_rapl(self) -> Tuple[Dict, List]:
        rapl_paths = {}
        rapl_domains = []
        for pattern in ["/sys/class/powercap/intel-rapl*",
                        "/sys/devices/virtual/powercap/intel-rapl*"]:
            for base in glob.glob(pattern):
                real_base = os.path.realpath(base) if os.path.islink(base) else base
                if not os.path.isdir(real_base):
                    continue
                energy_file = os.path.join(real_base, "energy_uj")
                name_file = os.path.join(real_base, "name")
                if not os.path.exists(energy_file):
                    continue
                domain_name = _read_file(name_file, os.path.basename(real_base))
                domain_name = domain_name.replace("intel-rapl:", "")
                if domain_name not in rapl_paths:
                    rapl_paths[domain_name] = energy_file
                    rapl_domains.append(domain_name)
        return rapl_paths, rapl_domains

    def _detect_msr_devices(self) -> List[str]:
        devices = []
        for cpu_id in range(os.cpu_count() or 8):
            p = f"/dev/cpu/{cpu_id}/msr"
            if os.path.exists(p):
                devices.append(p)
        return devices

    def _detect_cpufreq(self) -> Dict[str, str]:
        paths = {}
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
        if os.path.exists(base):
            for fname in ["scaling_cur_freq", "scaling_max_freq", "scaling_min_freq"]:
                p = os.path.join(base, fname)
                if os.path.exists(p):
                    paths[fname] = p
        return paths

    def _get_cpu_info_x86(self) -> dict:
        model = "Unknown"
        vendor_raw = "Unknown"
        microcode = "Unknown"
        physical = (os.cpu_count() or 8) // 2
        logical = os.cpu_count() or 8
        content = _read_file("/proc/cpuinfo", "")
        cores = set()
        for line in content.splitlines():
            if line.startswith("model name"):
                model = line.split(":")[1].strip()
            elif line.startswith("vendor_id"):
                vendor_raw = line.split(":")[1].strip()
            elif line.startswith("microcode"):
                microcode = line.split(":")[1].strip()
            elif line.startswith("core id"):
                cores.add(line.split(":")[-1].strip())
        if cores:
            physical = len(cores)
        return {
            "model": model, "vendor_raw": vendor_raw, "microcode": microcode,
            "physical_cores": physical, "logical_cores": logical,
            "cores_list": list(range(logical)),
        }

    def _get_cpu_flags(self) -> dict:
        flags_str = ""
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if line.startswith("flags"):
                flags_str = line.split(":")[1].strip()
                break
        return {
            "has_avx2": "avx2" in flags_str,
            "has_avx512": "avx512" in flags_str,
            "has_vmx": "vmx" in flags_str,
        }

    def _get_cpu_details(self) -> dict:
        details = {"family": None, "model": None, "stepping": None}
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if "cpu family" in line:
                try:
                    details["family"] = int(line.split(":")[1].strip())
                except ValueError:
                    pass
            elif line.startswith("model") and "model name" not in line:
                try:
                    details["model"] = int(line.split(":")[1].strip())
                except ValueError:
                    pass
            elif "stepping" in line:
                try:
                    details["stepping"] = int(line.split(":")[1].strip())
                except ValueError:
                    pass
        return details

    def _get_system_info_linux(self) -> dict:
        info = {"manufacturer": None, "product": None, "type": None, "virtualization": None}
        info["manufacturer"] = _read_file("/sys/class/dmi/id/sys_vendor")
        info["product"] = _read_file("/sys/class/dmi/id/product_name")
        chassis = _read_file("/sys/class/dmi/id/chassis_type")
        if chassis:
            chassis_map = {"3": "desktop", "4": "desktop", "8": "laptop",
                           "9": "laptop", "10": "laptop", "17": "server"}
            info["type"] = chassis_map.get(chassis, "unknown")
        info["virtualization"] = _detect_virtualization()
        return info

    def _get_gpu_info_linux(self) -> dict:
        gpu = {"model": None, "driver": None, "count": 0, "vendor": "unknown"}
        r = _run(["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"])
        if r and r.returncode == 0 and r.stdout.strip():
            gpu["model"] = r.stdout.strip().split("\n")[0].strip()
            gpu["vendor"] = "nvidia"
            gpu["driver"] = "nvidia"
            gpu["count"] = len(r.stdout.strip().split("\n"))
            return gpu
        r = _run(["lspci", "-nn"])
        if r and r.returncode == 0:
            for line in r.stdout.splitlines():
                if any(tag in line for tag in ("VGA", "Display", "3D")):
                    gpu["count"] += 1
                    if "NVIDIA" in line or "10de" in line.lower():
                        gpu["vendor"] = "nvidia"
                        gpu["model"] = line.split(":")[-1].strip()
                        gpu["driver"] = "nvidia"
                    elif "AMD" in line or "1002" in line.lower():
                        gpu["vendor"] = "amd"
                        gpu["model"] = line.split(":")[-1].strip()
                        gpu["driver"] = "amdgpu"
                    elif "Intel" in line or "8086" in line.lower():
                        gpu["vendor"] = "intel"
                        gpu["model"] = line.split(":")[-1].strip()
                        gpu["driver"] = "i915"
        return gpu

    def _detect_thermal_paths_x86(self) -> Tuple[Dict[str, str], Optional[str]]:
        thermal_paths = {}
        package_temp = None
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            temp_file = os.path.join(zone, "temp")
            type_file = os.path.join(zone, "type")
            if os.path.exists(temp_file) and os.path.exists(type_file):
                zone_type = _read_file(type_file, "")
                if zone_type:
                    thermal_paths[zone_type] = temp_file
                    if any(pkg in zone_type.lower() for pkg in ["pkg", "package", "x86"]):
                        package_temp = zone_type
        return thermal_paths, package_temp


# ============================================================================
# INTEL LINUX DETECTOR
# ============================================================================


class IntelLinuxDetector(PlatformDetector, LinuxX86Mixin):

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: Intel Linux x86_64")

        cpu_raw = self._get_cpu_info_x86()
        tsc_hz = self._detect_tsc()
        cpu_raw["tsc_frequency_hz"] = tsc_hz
        cpu_raw["tsc_detection_method"] = "auto_detected" if tsc_hz else "not_detected"
        cpu_raw["vendor"] = "intel"

        rapl_paths, rapl_domains = self._detect_rapl()
        self._log(f"RAPL: {len(rapl_paths)} domains")

        thermal_zones = discover_thermal_zones()
        hwmon_sensors = discover_hwmon_thermal()
        thermal_paths, package_temp = self._detect_thermal_paths_x86()
        sensors_to_monitor = classify_thermal_sensors(thermal_zones, hwmon_sensors, "intel")
        if "cpu_package" in sensors_to_monitor:
            package_temp = sensors_to_monitor["cpu_package"]

        msr_devices = self._detect_msr_devices()
        cpufreq = self._detect_cpufreq()
        turbo = self._detect_turbostat()
        ring_bus = self._detect_ring_bus()
        ring_bus["sysfs_paths"] = self._detect_ring_bus_sysfs_paths()
        msr_enhanced = self._enhance_msr({"devices": msr_devices, "count": len(msr_devices)})
        system_info = self._get_system_info_linux()
        gpu = self._get_gpu_info_linux()
        ram_gb = psutil.virtual_memory().total // (1024**3)

        config = {
            "metadata": self._metadata(),
            "os_name": "Linux",
            "cpu_architecture": "x86_64",
            "cpu_vendor": "intel",
            "platform_class": "intel_x86",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu_raw,
            "cpu_model": cpu_raw.get("model"),
            "cpu_cores": cpu_raw.get("physical_cores"),
            "cpu_vendor_raw": cpu_raw.get("vendor_raw"),
            "microcode_version": cpu_raw.get("microcode"),
            "ram_gb": ram_gb,
            "rapl": {"paths": rapl_paths, "available_domains": rapl_domains,
                     "has_dram": any("dram" in d.lower() for d in rapl_domains)},
            "thermal": {
                "paths": thermal_paths, "package_temp": package_temp,
                "discovered_zones": thermal_zones,
                "sensors_to_monitor": sensors_to_monitor,
                "sampling_rate_hz": 1,
            },
            "msr": msr_enhanced,
            "cpufreq": {"paths": cpufreq},
            "turbostat": turbo,
            "ring_bus": ring_bus,
            "cpu_flags": self._get_cpu_flags(),
            "cpu_details": self._get_cpu_details(),
            "gpu": gpu,
            "gpu_model": gpu.get("model"),
            "system": system_info,
            "system_manufacturer": system_info.get("manufacturer"),
            "system_product": system_info.get("product"),
            "system_type": system_info.get("type"),
            "virtualization_type": system_info.get("virtualization"),
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_tsc(self) -> Optional[int]:
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if "model name" in line:
                m = re.search(r"@ (\d+\.?\d*)\s*GHz", line)
                if m:
                    hz = int(float(m.group(1)) * 1_000_000_000)
                    self._log(f"TSC frequency (model name): {hz / 1e6:.0f} MHz")
                    return hz
        raw = _read_file("/sys/devices/system/cpu/cpu0/tsc_freq_khz")
        if raw:
            try:
                return int(raw) * 1000
            except ValueError:
                pass
        turbo_bin = self._find_turbostat_binary()
        if turbo_bin:
            r = _run([turbo_bin, "--quiet", "--show", "TSC_MHz",
                      "--interval", "1", "sleep", "1"], timeout=3)
            output = (r.stderr or r.stdout or "") if r else ""
            for line in output.strip().split("\n")[1:]:
                if line.strip():
                    try:
                        return int(float(line.strip().split()[0]) * 1_000_000)
                    except (ValueError, IndexError):
                        continue
        self._log("TSC frequency: not detected")
        return None

    def _find_turbostat_binary(self) -> Optional[str]:
        kernel_release = platform.release()
        real_path = f"/usr/lib/linux-tools/{kernel_release}/turbostat"
        if os.path.exists(real_path):
            return real_path
        r = _run(["dpkg", "-L", "linux-tools-common"])
        if r and r.returncode == 0:
            for line in r.stdout.splitlines():
                if "turbostat" in line and not line.endswith(".gz"):
                    if os.path.exists(line) and not os.path.islink(line):
                        return line
        wrapper = shutil.which("turbostat")
        if wrapper:
            real = os.path.realpath(wrapper)
            if os.path.exists(real):
                return real
        return None

    def _detect_turbostat(self) -> dict:
        tc = {"available": False, "columns": {}, "available_metrics": [],
              "raw_columns": [], "msr_access": False, "error": None}
        turbo_bin = self._find_turbostat_binary()
        if not turbo_bin:
            tc["error"] = "turbostat not installed"
            return tc
        try:
            cmd = [turbo_bin, "--Summary", "--quiet", "--select", "all",
                   "--interval", "1", "--num_iterations", "1"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                tc["error"] = "turbostat failed"
                return tc
            lines = r.stderr.strip().split("\n")
            header_line = None
            for line in lines:
                if "\t" in line and any(key in line for key in ("MHz", "C1", "CPU", "Pkg")):
                    header_line = line
                    break
            if not header_line:
                tc["error"] = "Could not find header line"
                return tc
            reader = csv.reader([header_line], delimiter="\t")
            available_columns = next(reader)
            tc["raw_columns"] = available_columns
            tc["available"] = True
            tc["msr_access"] = True

            wanted = {
                "c1_residency": ["C1ACPI%", "C1%", "POLL%"],
                "c2_residency": ["C2ACPI%", "C2%"],
                "c3_residency": ["C3ACPI%", "C3%"],
                "c6_residency": ["CPU%c6", "C6%"],
                "c7_residency": ["CPU%c7", "C7%"],
                "c8_residency": ["Pkg%pc8", "C8%"],
                "c9_residency": ["Pkg%pc9", "C9%"],
                "c10_residency": ["Pk%pc10", "Pkg%pc10", "C10%"],
                "gfx_rc6": ["GFX%rc6", "RC6%"],
                "gfx_freq": ["GFXMHz", "GTMHz"],
                "package_temp": ["PkgTmp", "PkgTemp", "CPU_Temp"],
                "bzy_mhz": ["Bzy_MHz"],
                "tsc_mhz": ["TSC_MHz"],
                "avg_mhz": ["Avg_MHz"],
                "pkg_watts": ["PkgWatt"],
                "cor_watts": ["CorWatt"],
                "gfx_watts": ["GFXWatt"],
                "ram_watts": ["RAMWatt"],
                "ipc": ["IPC"],
                "irq": ["IRQ"],
                "busy_percent": ["Busy%"],
            }
            column_mapping = {}
            available_metrics = []
            for metric, names in wanted.items():
                for name in names:
                    if name in available_columns:
                        column_mapping[metric] = name
                        if metric.startswith("c") and metric[1].isdigit():
                            c_num = metric[1:3].rstrip("_")
                            if c_num not in available_metrics:
                                available_metrics.append(c_num)
                        break
            for col in available_columns:
                if col.startswith("C") and "%" in col:
                    c_num = col.replace("C", "").replace("%", "").replace("ACPI", "")
                    if c_num.isdigit() and c_num not in available_metrics:
                        available_metrics.append(c_num)
                elif col.startswith("CPU%c"):
                    c_num = col.replace("CPU%c", "")
                    if c_num.isdigit() and c_num not in available_metrics:
                        available_metrics.append(c_num)
            tc["columns"] = column_mapping
            tc["available_metrics"] = sorted(list(set(available_metrics)))
            tc["detection_method"] = "csv_parser"
            self._log(f"Turbostat: {len(column_mapping)} columns detected")
        except Exception as e:
            tc["error"] = str(e)
        return tc

    def _detect_ring_bus(self) -> dict:
        limits = {"min_mhz": None, "max_mhz": None, "base_clock_mhz": None,
                  "detection_method": "unknown"}
        base_path = "/sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00"
        min_raw = _read_file(f"{base_path}/min_freq_khz")
        max_raw = _read_file(f"{base_path}/max_freq_khz")
        if min_raw and max_raw:
            try:
                limits["min_mhz"] = int(min_raw) / 1000.0
                limits["max_mhz"] = int(max_raw) / 1000.0
                limits["base_clock_mhz"] = 100.0
                limits["detection_method"] = "sysfs"
                return limits
            except ValueError:
                pass
        r = _run(["rdmsr", "0x621"], timeout=1)
        if r and r.returncode == 0:
            try:
                val = int(r.stdout.strip(), 16)
                limits["base_clock_mhz"] = 100.0
                min_ratio = val & 0x7F
                max_ratio = (val >> 8) & 0x7F
                if min_ratio > 0:
                    limits["min_mhz"] = min_ratio * 100.0
                if max_ratio > 0:
                    limits["max_mhz"] = max_ratio * 100.0
                limits["detection_method"] = "msr"
                return limits
            except ValueError:
                pass
        limits.setdefault("min_mhz", 400.0)
        limits.setdefault("max_mhz", 3600.0)
        limits.setdefault("base_clock_mhz", 100.0)
        return limits

    def _detect_ring_bus_sysfs_paths(self) -> dict:
        paths = {}
        base = "/sys/devices/system/cpu/intel_uncore_frequency"
        if not os.path.exists(base):
            return paths
        for pkg_dir in glob.glob(f"{base}/package_*_die_*"):
            if not os.path.isdir(pkg_dir):
                continue
            mappings = {
                "current_freq_khz": "current_freq",
                "initial_max_freq_khz": "initial_max_freq",
                "initial_min_freq_khz": "initial_min_freq",
                "max_freq_khz": "max_freq",
                "min_freq_khz": "min_freq",
            }
            for fname, key in mappings.items():
                fp = os.path.join(pkg_dir, fname)
                if os.path.exists(fp):
                    paths[key] = fp
        return paths

    def _enhance_msr(self, msr_config: dict) -> dict:
        if "cstate_counter_max" not in msr_config:
            msr_config["cstate_counter_max"] = 2**64 - 1
        if "cstate_counters" not in msr_config:
            counters = {}
            cstate_msrs = {"c2": 0x3F8, "c3": 0x3F9, "c6": 0x3FA, "c7": 0x3FB}
            for state, addr in cstate_msrs.items():
                r = _run(["rdmsr", f"0x{addr:X}"], timeout=1)
                counters[state] = {"address": f"0x{addr:X}",
                                   "available": r is not None and r.returncode == 0}
            msr_config["cstate_counters"] = counters
        if "ring_bus_base_clock_mhz" not in msr_config:
            msr_config["ring_bus_base_clock_mhz"] = 100.0
        if "wakeup_idle_ms" not in msr_config:
            msr_config["wakeup_idle_ms"] = 2
        return msr_config


# ============================================================================
# AMD LINUX DETECTOR
# ============================================================================


class AMDLinuxDetector(PlatformDetector, LinuxX86Mixin):

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: AMD Linux x86_64")

        cpu_raw = self._get_cpu_info_x86()
        tsc_hz = self._detect_tsc_amd()
        cpu_raw["tsc_frequency_hz"] = tsc_hz
        cpu_raw["tsc_detection_method"] = "auto_detected" if tsc_hz else "not_detected"
        cpu_raw["vendor"] = "amd"

        rapl_paths, rapl_domains = self._detect_rapl()
        self._log(f"RAPL: {len(rapl_paths)} domains (AMD uses intel-rapl namespace)")

        thermal_zones = discover_thermal_zones()
        hwmon_sensors = discover_hwmon_thermal()
        thermal_paths, package_temp = self._detect_thermal_paths_amd(hwmon_sensors)
        sensors_to_monitor = classify_thermal_sensors(thermal_zones, hwmon_sensors, "amd")
        if "cpu_package" in sensors_to_monitor:
            package_temp = sensors_to_monitor["cpu_package"]

        msr_devices = self._detect_msr_devices()
        cpufreq = self._detect_cpufreq()
        system_info = self._get_system_info_linux()
        gpu = self._get_gpu_info_linux()
        ram_gb = psutil.virtual_memory().total // (1024**3)

        config = {
            "metadata": self._metadata(),
            "os_name": "Linux",
            "cpu_architecture": "x86_64",
            "cpu_vendor": "amd",
            "platform_class": "amd_x86",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu_raw,
            "cpu_model": cpu_raw.get("model"),
            "cpu_cores": cpu_raw.get("physical_cores"),
            "cpu_vendor_raw": cpu_raw.get("vendor_raw"),
            "microcode_version": cpu_raw.get("microcode"),
            "ram_gb": ram_gb,
            "rapl": {"paths": rapl_paths, "available_domains": rapl_domains,
                     "has_dram": any("dram" in d.lower() for d in rapl_domains)},
            "thermal": {
                "paths": thermal_paths, "package_temp": package_temp,
                "discovered_zones": thermal_zones,
                "sensors_to_monitor": sensors_to_monitor,
                "sampling_rate_hz": 1,
            },
            "msr": {"devices": msr_devices, "count": len(msr_devices)},
            "cpufreq": {"paths": cpufreq},
            # Turbostat disabled: observed SIGABRT on Ryzen 5 3600 (Zen 2).
            # May work on future AMD generations; validate before enabling.
            "turbostat": {"available": False, "columns": {},
                          "error": "disabled for this AMD platform (observed crash on Zen 2)"},
            "ring_bus": {"min_mhz": None, "max_mhz": None,
                         "base_clock_mhz": None, "sysfs_paths": {}},
            "cpu_flags": self._get_cpu_flags(),
            "cpu_details": self._get_cpu_details(),
            "gpu": gpu,
            "gpu_model": gpu.get("model"),
            "system": system_info,
            "system_manufacturer": system_info.get("manufacturer"),
            "system_product": system_info.get("product"),
            "system_type": system_info.get("type"),
            "virtualization_type": system_info.get("virtualization"),
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_tsc_amd(self) -> Optional[int]:
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if "model name" in line:
                m = re.search(r"@ (\d+\.?\d*)\s*GHz", line)
                if m:
                    return int(float(m.group(1)) * 1_000_000_000)
        raw = _read_file("/sys/devices/system/cpu/cpu0/tsc_freq_khz")
        if raw:
            try:
                return int(raw) * 1000
            except ValueError:
                pass
        return None

    def _detect_thermal_paths_amd(self, hwmon_sensors: dict) -> Tuple[Dict[str, str], Optional[str]]:
        thermal_paths = {}
        package_temp = None
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            temp_file = os.path.join(zone, "temp")
            type_file = os.path.join(zone, "type")
            if os.path.exists(temp_file) and os.path.exists(type_file):
                zone_type = _read_file(type_file, "")
                if zone_type:
                    thermal_paths[zone_type] = temp_file
        if "k10temp" in hwmon_sensors:
            thermal_paths["k10temp"] = hwmon_sensors["k10temp"]
            package_temp = "k10temp"
            self._log("Thermal: k10temp hwmon found (AMD package temp)")
        return thermal_paths, package_temp


# ============================================================================
# GENERIC X86 LINUX DETECTOR (unknown vendor: Hygon, VIA, Zhaoxin, VM, etc.)
# ============================================================================


class GenericX86LinuxDetector(PlatformDetector, LinuxX86Mixin):
    """
    Fallback for x86_64 Linux where vendor is not Intel or AMD.
    Covers: Hygon, VIA, Zhaoxin, VMs reporting unusual vendor strings,
    CloudLab servers, etc. Uses safe probes only (no Intel-specific MSR
    enhancement, no AMD turbostat skip assumption).
    """

    def detect(self) -> Dict[str, Any]:
        cpu_raw = self._get_cpu_info_x86()
        vendor_raw = cpu_raw.get("vendor_raw", "Unknown")
        self._log(f"Platform: Generic x86_64 Linux (vendor: {vendor_raw})")

        cpu_raw["tsc_frequency_hz"] = self._detect_tsc_generic()
        cpu_raw["tsc_detection_method"] = (
            "auto_detected" if cpu_raw["tsc_frequency_hz"] else "not_detected"
        )
        cpu_raw["vendor"] = "unknown"

        rapl_paths, rapl_domains = self._detect_rapl()
        thermal_zones = discover_thermal_zones()
        hwmon_sensors = discover_hwmon_thermal()
        thermal_paths, package_temp = self._detect_thermal_paths_x86()
        sensors_to_monitor = classify_thermal_sensors(thermal_zones, hwmon_sensors, "unknown")
        if "cpu_package" in sensors_to_monitor:
            package_temp = sensors_to_monitor["cpu_package"]

        msr_devices = self._detect_msr_devices()
        cpufreq = self._detect_cpufreq()
        system_info = self._get_system_info_linux()
        gpu = self._get_gpu_info_linux()
        ram_gb = psutil.virtual_memory().total // (1024**3)

        config = {
            "metadata": self._metadata(),
            "os_name": "Linux",
            "cpu_architecture": "x86_64",
            "cpu_vendor": "unknown",
            "platform_class": "linux_x86_unknown",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu_raw,
            "cpu_model": cpu_raw.get("model"),
            "cpu_cores": cpu_raw.get("physical_cores"),
            "cpu_vendor_raw": vendor_raw,
            "microcode_version": cpu_raw.get("microcode"),
            "ram_gb": ram_gb,
            "rapl": {"paths": rapl_paths, "available_domains": rapl_domains,
                     "has_dram": any("dram" in d.lower() for d in rapl_domains)},
            "thermal": {
                "paths": thermal_paths, "package_temp": package_temp,
                "discovered_zones": thermal_zones,
                "sensors_to_monitor": sensors_to_monitor,
                "sampling_rate_hz": 1,
            },
            "msr": {"devices": msr_devices, "count": len(msr_devices)},
            "cpufreq": {"paths": cpufreq},
            "turbostat": {"available": False, "columns": {},
                          "error": f"turbostat not attempted for unknown vendor: {vendor_raw}"},
            "ring_bus": {"min_mhz": None, "max_mhz": None,
                         "base_clock_mhz": None, "sysfs_paths": {}},
            "cpu_flags": self._get_cpu_flags(),
            "cpu_details": self._get_cpu_details(),
            "gpu": gpu,
            "gpu_model": gpu.get("model"),
            "system": system_info,
            "system_manufacturer": system_info.get("manufacturer"),
            "system_product": system_info.get("product"),
            "system_type": system_info.get("type"),
            "virtualization_type": system_info.get("virtualization"),
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_tsc_generic(self) -> Optional[int]:
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if "model name" in line:
                m = re.search(r"@ (\d+\.?\d*)\s*GHz", line)
                if m:
                    return int(float(m.group(1)) * 1_000_000_000)
        raw = _read_file("/sys/devices/system/cpu/cpu0/tsc_freq_khz")
        if raw:
            try:
                return int(raw) * 1000
            except ValueError:
                pass
        return None


# ============================================================================
# ARM LINUX BASE CLASS (shared by Grace and Generic ARM)
# ============================================================================


class ARMLinuxBase(PlatformDetector):
    """Common probes for all aarch64 Linux platforms."""

    def _get_cpu_info_arm(self) -> dict:
        model = "Unknown"
        physical = os.cpu_count() or 8
        logical = physical  # ARM typically no SMT
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if line.startswith("model name") or line.startswith("Model name"):
                model = line.split(":")[1].strip()
                break
        self._log(f"CPU: {model} ({physical} cores)")
        return {
            "model": model, "vendor": "arm_unknown", "microcode": "N/A",
            "physical_cores": physical, "logical_cores": logical,
            "cores_list": list(range(logical)),
            "tsc_frequency_hz": None, "tsc_detection_method": "not_applicable",
        }

    def _get_system_info_arm(self) -> dict:
        info = {"manufacturer": None, "product": None, "type": None, "virtualization": None}
        info["manufacturer"] = _read_file("/sys/class/dmi/id/sys_vendor")
        info["product"] = _read_file("/sys/class/dmi/id/product_name")
        chassis = _read_file("/sys/class/dmi/id/chassis_type")
        if chassis:
            chassis_map = {"3": "desktop", "4": "desktop", "17": "server"}
            info["type"] = chassis_map.get(chassis, "unknown")
        info["virtualization"] = _detect_virtualization()
        return info

    def _detect_gpu_arm(self) -> dict:
        gpu = {"model": None, "driver": None, "count": 0, "vendor": "unknown"}
        r = _run(["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"])
        if r and r.returncode == 0 and r.stdout.strip():
            gpu["model"] = r.stdout.strip().split("\n")[0].strip()
            gpu["vendor"] = "nvidia"
            gpu["driver"] = "nvidia"
            gpu["count"] = len(r.stdout.strip().split("\n"))
        return gpu

    def _detect_cpufreq_arm(self) -> Dict[str, str]:
        paths = {}
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
        if os.path.exists(base):
            for fname in ["scaling_cur_freq", "scaling_max_freq", "scaling_min_freq"]:
                p = os.path.join(base, fname)
                if os.path.exists(p):
                    paths[fname] = p
        return paths

    def _detect_thermal_paths_arm(self) -> Tuple[Dict[str, str], Optional[str]]:
        thermal_paths = {}
        package_temp = None
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            temp_file = os.path.join(zone, "temp")
            type_file = os.path.join(zone, "type")
            if os.path.exists(temp_file) and os.path.exists(type_file):
                zt = _read_file(type_file, "")
                if zt:
                    thermal_paths[zt] = temp_file
        return thermal_paths, package_temp

    def _detect_arm_pmu(self) -> dict:
        result = {"available": False, "events": [],
                  "has_generic_events": False, "has_armv8_events": False}
        r = _run(["perf", "stat", "-e", "instructions,cycles", "--", "true"], timeout=5)
        if r and "instructions" in (r.stderr or ""):
            result["has_generic_events"] = True
            result["events"].extend(["instructions", "cycles"])
        r = _run(["perf", "list", "armv8_pmuv3"], timeout=5)
        if r and "armv8_pmuv3" in (r.stdout or ""):
            result["has_armv8_events"] = True
            for line in r.stdout.splitlines():
                if "armv8_pmuv3/" in line:
                    result["events"].append(line.strip().split()[0])
        result["available"] = result["has_generic_events"] or result["has_armv8_events"]
        return result

    def _detect_arm_cpuidle(self) -> dict:
        result = {"available": False, "states": [], "paths": {}}
        for state_dir in sorted(glob.glob("/sys/devices/system/cpu/cpu0/cpuidle/state*/")):
            name = _read_file(state_dir + "name")
            if name:
                time_path = state_dir + "time"
                if os.path.exists(time_path):
                    result["states"].append(name)
                    result["paths"][name] = time_path
        result["available"] = len(result["states"]) > 0
        return result

    def _build_arm_config(self, cpu_vendor: str, platform_class: str) -> Dict[str, Any]:
        """Assemble the common parts of an ARM config dict."""
        cpu = self._get_cpu_info_arm()
        cpu["vendor"] = cpu_vendor
        gpu = self._detect_gpu_arm()
        system_info = self._get_system_info_arm()
        ram_gb = psutil.virtual_memory().total // (1024**3)

        thermal_zones = discover_thermal_zones()
        hwmon_sensors = discover_hwmon_thermal()
        thermal_paths, package_temp = self._detect_thermal_paths_arm()
        sensors_to_monitor = classify_thermal_sensors(
            thermal_zones, hwmon_sensors, cpu_vendor
        )

        return {
            "metadata": self._metadata(),
            "os_name": "Linux",
            "cpu_architecture": "aarch64",
            "cpu_vendor": cpu_vendor,
            "platform_class": platform_class,
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu,
            "cpu_model": cpu.get("model"),
            "cpu_cores": cpu.get("physical_cores"),
            "cpu_vendor_raw": cpu_vendor,
            "microcode_version": "N/A",
            "ram_gb": ram_gb,
            "rapl": {"paths": {}, "available_domains": [], "has_dram": False},
            "thermal": {
                "paths": thermal_paths, "package_temp": package_temp,
                "discovered_zones": thermal_zones,
                "sensors_to_monitor": sensors_to_monitor,
                "sampling_rate_hz": 1,
            },
            "msr": {"devices": [], "count": 0},
            "cpufreq": {"paths": self._detect_cpufreq_arm()},
            "turbostat": {"available": False, "columns": {},
                          "error": "not applicable on ARM"},
            "ring_bus": {"min_mhz": None, "max_mhz": None,
                         "base_clock_mhz": None, "sysfs_paths": {}},
            "cpu_flags": {"has_avx2": False, "has_avx512": False, "has_vmx": False},
            "cpu_details": {"family": None, "model": None, "stepping": None},
            "gpu": gpu,
            "gpu_model": gpu.get("model"),
            "system": system_info,
            "system_manufacturer": system_info.get("manufacturer"),
            "system_product": system_info.get("product"),
            "system_type": system_info.get("type"),
            "virtualization_type": system_info.get("virtualization"),
            "arm_pmu": self._detect_arm_pmu(),
            "cpuidle": self._detect_arm_cpuidle(),
        }


# ============================================================================
# NVIDIA GRACE DETECTOR
# ============================================================================


class NVIDIAGraceDetector(ARMLinuxBase):

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: NVIDIA Grace (Linux aarch64)")
        config = self._build_arm_config("nvidia_grace", "nvidia_grace")

        # Grace-specific: update CPU model from DMI if /proc/cpuinfo was generic
        if config["cpu"]["model"] == "Unknown":
            product = _read_file("/sys/class/dmi/id/product_name", "")
            if product:
                config["cpu"]["model"] = f"NVIDIA Grace ({product})"
                config["cpu_model"] = config["cpu"]["model"]

        # Grace-specific probes
        config["spbm"] = self._detect_spbm()
        config["dcgm"] = self._detect_dcgm()
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_spbm(self) -> dict:
        result = {"available": False, "hwmon_path": "",
                  "energy_channels": [], "power_channels": [],
                  "energy_paths": {}, "power_paths": {}}
        for hwmon_dir in sorted(glob.glob("/sys/class/hwmon/hwmon*/")):
            name = _read_file(hwmon_dir + "name", "")
            if "spbm" not in name.lower() and "spark" not in name.lower():
                continue
            result["hwmon_path"] = hwmon_dir
            for label_file in sorted(glob.glob(hwmon_dir + "energy*_label")):
                label = _read_file(label_file)
                if label:
                    input_file = label_file.replace("_label", "_input")
                    if os.path.exists(input_file):
                        result["energy_channels"].append(label)
                        result["energy_paths"][label] = input_file
            for label_file in sorted(glob.glob(hwmon_dir + "power*_label")):
                label = _read_file(label_file)
                if label:
                    input_file = label_file.replace("_label", "_input")
                    if os.path.exists(input_file):
                        result["power_channels"].append(label)
                        result["power_paths"][label] = input_file
            result["available"] = len(result["energy_channels"]) > 0
            self._log(f"SPBM: {'found' if result['available'] else 'no energy channels'}")
            break
        return result

    def _detect_dcgm(self) -> dict:
        result = {"available": False, "daemon_running": False,
                  "field_156_verified": False, "field_155_verified": False}
        r = _run(["which", "dcgmi"])
        if not r or r.returncode != 0:
            return result
        result["available"] = True
        r = _run(["dcgmi", "discovery", "-l"], timeout=5)
        if not r or r.returncode != 0:
            return result
        result["daemon_running"] = True
        r = _run(["dcgmi", "dmon", "-e", "155,156", "-c", "1"], timeout=10)
        if r and r.returncode == 0 and "GPU" in (r.stdout or ""):
            result["field_156_verified"] = True
            result["field_155_verified"] = True
            self._log("DCGM: field 155+156 verified")
        return result


# ============================================================================
# GENERIC ARM LINUX DETECTOR
# ============================================================================


class GenericARMLinuxDetector(ARMLinuxBase):

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: Generic ARM Linux (aarch64)")
        config = self._build_arm_config("arm_unknown", "linux_arm")
        # Generic ARM: SPBM/DCGM not expected
        config["spbm"] = {"available": False}
        config["dcgm"] = {"available": False}
        config["hardware_hash"] = generate_hardware_hash(config)
        return config


# ============================================================================
# RISC-V LINUX DETECTOR
# ============================================================================


class RISCVLinuxDetector(PlatformDetector):
    """RISC-V Linux. Minimal probes: /proc/cpuinfo, cpufreq, thermal_zone."""

    def detect(self) -> Dict[str, Any]:
        self._log("Platform: RISC-V Linux (riscv64)")
        cpu = self._detect_cpu()
        system_info = self._get_system_info()
        ram_gb = psutil.virtual_memory().total // (1024**3)

        thermal_zones = discover_thermal_zones()
        hwmon_sensors = discover_hwmon_thermal()
        thermal_paths = {}
        for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
            temp_file = os.path.join(zone, "temp")
            type_file = os.path.join(zone, "type")
            if os.path.exists(temp_file) and os.path.exists(type_file):
                zt = _read_file(type_file, "")
                if zt:
                    thermal_paths[zt] = temp_file
        sensors_to_monitor = classify_thermal_sensors(
            thermal_zones, hwmon_sensors, "riscv"
        )

        cpufreq = {}
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
        if os.path.exists(base):
            for fname in ["scaling_cur_freq", "scaling_max_freq", "scaling_min_freq"]:
                p = os.path.join(base, fname)
                if os.path.exists(p):
                    cpufreq[fname] = p

        config = {
            "metadata": self._metadata(),
            "os_name": "Linux",
            "cpu_architecture": "riscv64",
            "cpu_vendor": "riscv",
            "platform_class": "linux_riscv",
            "hw_config_version": HW_CONFIG_SCHEMA_VERSION,
            "config_version": LEGACY_CONFIG_VERSION,
            "cpu": cpu,
            "cpu_model": cpu.get("model"),
            "cpu_cores": cpu.get("physical_cores"),
            "cpu_vendor_raw": cpu.get("vendor_raw", "riscv"),
            "microcode_version": "N/A",
            "ram_gb": ram_gb,
            "rapl": {"paths": {}, "available_domains": [], "has_dram": False},
            "thermal": {
                "paths": thermal_paths, "package_temp": None,
                "discovered_zones": thermal_zones,
                "sensors_to_monitor": sensors_to_monitor,
                "sampling_rate_hz": 1,
            },
            "msr": {"devices": [], "count": 0},
            "cpufreq": {"paths": cpufreq},
            "turbostat": {"available": False, "columns": {},
                          "error": "not applicable on RISC-V"},
            "ring_bus": {"min_mhz": None, "max_mhz": None,
                         "base_clock_mhz": None, "sysfs_paths": {}},
            "cpu_flags": {"has_avx2": False, "has_avx512": False, "has_vmx": False},
            "cpu_details": {"family": None, "model": None, "stepping": None},
            "gpu": {"model": None, "driver": None, "count": 0, "vendor": "unknown"},
            "gpu_model": None,
            "system": system_info,
            "system_manufacturer": system_info.get("manufacturer"),
            "system_product": system_info.get("product"),
            "system_type": system_info.get("type"),
            "virtualization_type": system_info.get("virtualization"),
            "spbm": {"available": False},
            "dcgm": {"available": False},
            "arm_pmu": {"available": False, "events": []},
            "cpuidle": {"available": False, "states": [], "paths": {}},
        }
        config["hardware_hash"] = generate_hardware_hash(config)
        return config

    def _detect_cpu(self) -> dict:
        model = "Unknown"
        physical = os.cpu_count() or 1
        logical = physical
        content = _read_file("/proc/cpuinfo", "")
        for line in content.splitlines():
            if line.startswith("model name") or line.startswith("uarch"):
                model = line.split(":")[1].strip()
                break
        return {
            "model": model, "vendor": "riscv", "vendor_raw": "riscv",
            "microcode": "N/A",
            "physical_cores": physical, "logical_cores": logical,
            "cores_list": list(range(logical)),
            "tsc_frequency_hz": None, "tsc_detection_method": "not_applicable",
        }

    def _get_system_info(self) -> dict:
        info = {"manufacturer": None, "product": None, "type": None, "virtualization": None}
        info["manufacturer"] = _read_file("/sys/class/dmi/id/sys_vendor")
        info["product"] = _read_file("/sys/class/dmi/id/product_name")
        info["virtualization"] = _detect_virtualization()
        return info


# ============================================================================
# CONFIG MERGE (backward compatible)
# ============================================================================


def merge_configs(existing: dict, new_config: dict) -> dict:
    """
    Merge new detection into existing hw_config.json.
    Hardware-detected sections: new wins.
    User-customized fields: preserved.
    """
    merged = existing.copy()

    # Sections fully replaced by detection
    replace_keys = [
        "metadata", "rapl", "thermal", "cpufreq", "turbostat",
        "cpu_flags", "cpu_details", "gpu", "ring_bus", "system",
        "spbm", "dcgm", "arm_pmu", "cpuidle",
    ]
    for key in replace_keys:
        if key in new_config:
            merged[key] = new_config[key]

    # Top-level flat fields from detection
    flat_keys = [
        "os_name", "cpu_architecture", "cpu_vendor", "platform_class",
        "hw_config_version", "cpu_model", "cpu_cores", "ram_gb",
        "gpu_model", "cpu_vendor_raw", "microcode_version",
        "system_manufacturer", "system_product", "system_type",
        "virtualization_type",
    ]
    for key in flat_keys:
        if key in new_config:
            merged[key] = new_config[key]

    # CPU section: merge (preserve custom, update detected)
    new_cpu = new_config.get("cpu", {})
    if "cpu" not in merged:
        merged["cpu"] = {}
    for key in ["physical_cores", "logical_cores", "cores_list", "model",
                "vendor", "tsc_frequency_hz", "tsc_detection_method"]:
        if key in new_cpu:
            merged["cpu"][key] = new_cpu[key]

    # MSR section: preserve custom fields, update detected
    existing_msr = merged.get("msr", {})
    new_msr = new_config.get("msr", {})
    for key, val in new_msr.items():
        existing_msr[key] = val
    merged["msr"] = existing_msr

    # Version fields
    merged["config_version"] = max(merged.get("config_version", 1), LEGACY_CONFIG_VERSION)
    merged["hw_config_version"] = new_config.get("hw_config_version", HW_CONFIG_SCHEMA_VERSION)

    merged["hardware_hash"] = generate_hardware_hash(merged)
    return merged


# ============================================================================
# VALIDATION (structural + semantic)
# ============================================================================


def validate_config(config: dict) -> List[str]:
    """
    Validate hw_config.json for structural completeness and semantic
    consistency. Returns list of error strings (empty = valid).
    """
    errors = []

    # ---- Structural: required keys present and non-null ----
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in config or config[key] is None:
            errors.append(f"missing or null required key: {key}")

    if not config.get("cpu", {}).get("model"):
        errors.append("cpu.model is missing or empty")

    if "thermal" not in config:
        errors.append("thermal section missing")

    # ---- Semantic: cross-field consistency ----
    vendor = config.get("cpu_vendor")
    pclass = config.get("platform_class")
    if vendor and pclass and (vendor, pclass) not in VALID_VENDOR_PLATFORM:
        errors.append(
            f"cpu_vendor/platform_class mismatch: ({vendor}, {pclass}). "
            f"Valid pairs: {VALID_VENDOR_PLATFORM}"
        )

    arch = config.get("cpu_architecture")
    if pclass and arch:
        # x86 platforms must have x86_64 arch
        if "x86" in pclass and arch != "x86_64":
            errors.append(f"platform_class={pclass} but cpu_architecture={arch}")
        # ARM platforms must have aarch64/arm64
        if pclass in ("nvidia_grace", "linux_arm") and arch not in ("aarch64", "arm64"):
            errors.append(f"platform_class={pclass} but cpu_architecture={arch}")

    cores = config.get("cpu", {}).get("physical_cores", 0)
    if isinstance(cores, int) and cores <= 0:
        errors.append(f"cpu.physical_cores={cores} (must be > 0)")

    ram = config.get("ram_gb", 0)
    if isinstance(ram, (int, float)) and ram <= 0:
        errors.append(f"ram_gb={ram} (must be > 0)")

    return errors


# ============================================================================
# MAIN
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="A-LEMS Hardware Detection Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  # Default: detect and write config/hw_config.json (merges if exists)
  python3 scripts/detect_hardware.py

  # With sudo for full MSR/turbostat access
  sudo python3 scripts/detect_hardware.py

  # Custom output path
  python3 scripts/detect_hardware.py --output /tmp/hw.json

  # Print to stdout only, no file written
  python3 scripts/detect_hardware.py --stdout

  # Overwrite instead of merge
  python3 scripts/detect_hardware.py --no-merge
        """,
    )
    parser.add_argument("--output", "-o", type=str,
                        help="Output file path (default: config/hw_config.json)")
    parser.add_argument("--stdout", action="store_true",
                        help="Print JSON to stdout, do not write file")
    parser.add_argument("--no-merge", action="store_true",
                        help="Overwrite existing config instead of merging")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--backup-count", type=int, default=5,
                        help="Number of backups to keep (default: 5)")
    parser.add_argument("--no-backup", action="store_true",
                        help="Disable automatic backup")
    # Backward compat: accept --merge silently (now default)
    parser.add_argument("--merge", "-m", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # ---- Detect ----
    if args.verbose:
        print("\n  Detecting hardware...")

    detector = PlatformDetector.for_current_platform(verbose=args.verbose)
    new_config = detector.detect()

    # ---- stdout mode ----
    if args.stdout:
        print(json.dumps(new_config, indent=2))
        return 0

    # ---- Output path ----
    output_path = Path(args.output) if args.output else _default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- Backup ----
    if output_path.exists() and not args.no_backup:
        bp = create_backup(output_path, args.backup_count)
        if args.verbose and bp:
            print(f"  Backup: {bp}")

    # ---- Merge or overwrite ----
    if output_path.exists() and not args.no_merge:
        if args.verbose:
            print(f"  Merging with existing: {output_path}")
        try:
            with open(output_path) as f:
                existing = json.load(f)
            final_config = merge_configs(existing, new_config)
        except Exception as e:
            print(f"  Error reading existing config: {e}")
            print("  Creating new config instead")
            final_config = new_config
    else:
        final_config = new_config

    # ---- Write ----
    try:
        with open(output_path, "w") as f:
            json.dump(final_config, f, indent=2)
        print(f"  Hardware config saved to: {output_path}")
    except PermissionError:
        print(f"  Permission denied writing to {output_path}")
        print(f"  Try: sudo chown $USER:$USER {output_path.parent}")
        return 1

    # ---- Validate ----
    errors = validate_config(final_config)
    if errors:
        print("  WARNING: config validation issues:")
        for err in errors:
            print(f"    {err}")
        if args.no_merge:
            return 1

    # ---- Summary ----
    if args.verbose:
        print(f"\n  Summary:")
        print(f"    Platform: {final_config.get('platform_class')}")
        print(f"    CPU: {final_config.get('cpu_model')}")
        print(f"    Vendor: {final_config.get('cpu_vendor')}")
        virt = final_config.get("virtualization_type")
        if virt:
            print(f"    Virtualization: {virt}")
        print(f"    OS: {final_config.get('os_name')}")
        print(f"    Arch: {final_config.get('cpu_architecture')}")
        print(f"    RAPL: {len(final_config.get('rapl', {}).get('paths', {}))} domains")
        t = final_config.get("thermal", {})
        print(f"    Thermal: {len(t.get('paths', {}))} zones, "
              f"{len(t.get('sensors_to_monitor', {}))} monitored")
        print(f"    MSR: {final_config.get('msr', {}).get('count', 0)} devices")
        ts = final_config.get("turbostat", {})
        if ts.get("available"):
            print(f"    Turbostat: {len(ts.get('columns', {}))} columns")
        else:
            print(f"    Turbostat: {ts.get('error', 'not available')}")
        tsc = final_config.get("cpu", {}).get("tsc_frequency_hz")
        if tsc:
            print(f"    TSC: {tsc / 1e6:.0f} MHz")
        print(f"    Schema: hw_config_version={final_config.get('hw_config_version')}")
        print(f"    Hash: {final_config.get('hardware_hash')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())