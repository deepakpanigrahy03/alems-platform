"""
Execution context detection for A-LEMS.
Determines whether the machine is bare metal, a VM guest, a container, or WSL2.
Cloud provider is detected independently (orthogonal to execution context).

Governing invariant: detection uses system interfaces (systemd-detect-virt,
DMI, procfs, filesystem markers). Tool availability is never used as proof
of execution context.
"""

import os
import subprocess
from typing import Dict, Optional


def _run_quiet(cmd, timeout=3):
    """Run a subprocess, return stdout string or None on any failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


def _read_file(path, default=None):
    """Read a small file, return stripped content or default."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def detect_execution_context() -> str:
    """
    Determine virtualization layer between this OS and the hardware.

    Detection order (most specific to least specific):
    1. systemd-detect-virt (most reliable on systemd Linux)
    2. Container filesystem markers (/.dockerenv, /run/.containerenv)
    3. /proc/version for WSL2
    4. DMI product_name and sys_vendor for VM type
    5. All negative -> bare_metal

    Returns one of: bare_metal, kvm_guest, vmware_guest, hyperv_guest,
    container, wsl2, unknown
    """
    # 1. systemd-detect-virt
    virt = _run_quiet(["systemd-detect-virt"])
    if virt:
        if virt == "none":
            return "bare_metal"
        if virt == "kvm":
            return "kvm_guest"
        if virt == "vmware":
            return "vmware_guest"
        if virt in ("microsoft", "hyperv"):
            return "hyperv_guest"
        if virt == "wsl":
            return "wsl2"
        if virt in ("docker", "podman", "lxc", "lxc-libvirt",
                     "systemd-nspawn", "rkt"):
            return "container"
        if virt in ("qemu", "bochs", "xen", "uml", "parallels",
                     "bhyve", "acrn"):
            return "kvm_guest"
        # Unknown virt type from systemd, still virtualized
        return "kvm_guest"

    # 2. Container markers
    if os.path.exists("/.dockerenv"):
        return "container"
    if os.path.exists("/run/.containerenv"):
        return "container"
    # cgroup namespace check (containers often have cgroup v2 with limited scope)
    cgroup = _read_file("/proc/1/cgroup", "")
    if "docker" in cgroup or "kubepods" in cgroup or "containerd" in cgroup:
        return "container"

    # 3. WSL2 check via /proc/version
    proc_version = _read_file("/proc/version", "")
    if "microsoft" in proc_version.lower() or "wsl" in proc_version.lower():
        return "wsl2"

    # 4. DMI fallback for VM type
    product = _read_file("/sys/class/dmi/id/product_name", "").lower()
    manufacturer = _read_file("/sys/class/dmi/id/sys_vendor", "").lower()
    bios = _read_file("/sys/class/dmi/id/bios_vendor", "").lower()

    vm_signals = {
        "kvm_guest": ["kvm", "qemu"],
        "vmware_guest": ["vmware"],
        "hyperv_guest": ["hyper-v", "microsoft"],
        # VirtualBox and Parallels map to kvm_guest (similar capability profile)
    }
    combined = f"{product} {manufacturer} {bios}"
    for context, keywords in vm_signals.items():
        for kw in keywords:
            if kw in combined:
                return context
    if any(kw in combined for kw in ["virtualbox", "innotek",
                                      "parallels", "bochs", "xen"]):
        return "kvm_guest"

    # 5. If /sys/class/dmi exists and no VM signals, likely bare metal
    if os.path.exists("/sys/class/dmi/id/product_name"):
        return "bare_metal"

    # macOS: no /sys, no systemd, not a VM scenario A-LEMS cares about
    import platform
    if platform.system() == "Darwin":
        return "bare_metal"

    # Cannot determine
    return "unknown"


def detect_cloud_provider() -> Optional[str]:
    """
    Determine cloud hosting provider from DMI metadata.
    Independent of execution_context (a cloud VM is kvm_guest + cloud_provider=aws).

    Returns: aws, gcp, azure, oracle, hetzner, other, or None.
    """
    manufacturer = _read_file("/sys/class/dmi/id/sys_vendor", "").lower()
    product = _read_file("/sys/class/dmi/id/product_name", "").lower()
    bios = _read_file("/sys/class/dmi/id/bios_vendor", "").lower()
    combined = f"{manufacturer} {product} {bios}"

    if "amazon" in combined or "ec2" in combined:
        return "aws"
    if "google" in combined and "compute" in combined:
        return "gcp"
    # Azure: manufacturer is "Microsoft Corporation" but that also matches
    # Hyper-V desktop. Check for Azure-specific product strings.
    if "microsoft" in manufacturer and any(
        az in product for az in ["virtual machine", "azure"]
    ):
        return "azure"
    if "oracle" in combined:
        return "oracle"
    if "hetzner" in combined:
        return "hetzner"
    if "digitalocean" in combined:
        return "other"
    if "linode" in combined or "akamai" in combined:
        return "other"
    if "vultr" in combined:
        return "other"

    return None


def get_execution_context_evidence() -> Dict:
    """
    Return the raw detection signals for reproducibility.
    Stored in hw_config.json so a future investigator can see
    WHY A-LEMS classified this machine the way it did.
    """
    evidence = {"method": "unknown", "raw_output": None}

    # Try systemd-detect-virt first
    virt = _run_quiet(["systemd-detect-virt"])
    if virt is not None:
        evidence["method"] = "systemd-detect-virt"
        evidence["raw_output"] = virt
        return evidence

    # Container markers
    if os.path.exists("/.dockerenv"):
        evidence["method"] = "dockerenv_file"
        evidence["raw_output"] = "/.dockerenv exists"
        return evidence
    if os.path.exists("/run/.containerenv"):
        evidence["method"] = "containerenv_file"
        evidence["raw_output"] = "/run/.containerenv exists"
        return evidence

    # WSL2
    proc_version = _read_file("/proc/version", "")
    if "microsoft" in proc_version.lower():
        evidence["method"] = "proc_version"
        evidence["raw_output"] = proc_version[:200]
        return evidence

    # DMI
    product = _read_file("/sys/class/dmi/id/product_name")
    manufacturer = _read_file("/sys/class/dmi/id/sys_vendor")
    if product or manufacturer:
        evidence["method"] = "dmi"
        evidence["raw_output"] = f"vendor={manufacturer}, product={product}"
        return evidence

    # macOS
    import platform
    if platform.system() == "Darwin":
        evidence["method"] = "platform.system"
        evidence["raw_output"] = "Darwin"
        return evidence

    evidence["method"] = "none"
    evidence["raw_output"] = "all detection methods inconclusive"
    return evidence
