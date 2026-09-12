# Platform Detection Architecture

A-LEMS runs on 8 platform classes across 4 ISAs. Every platform has different
energy counters, different permission models, and different measurement
capabilities. The platform detection system resolves all of this at install
time and at experiment startup, so no other part of the codebase ever needs
to know what hardware it is running on.

---

## Design Principle

Platform identity is determined by hardware signals, not by OS or architecture
strings. `uname -m` returning `aarch64` does not tell you whether you have an
NVIDIA Grace SoC, a Graviton VM, a Raspberry Pi, or an Oracle ARM instance.
Each of these has a completely different energy stack. `platform_class` is the
single identifier that resolves this ambiguity across the entire system:
install logic, migration seed selection, reader factory routing, experiment
runner configuration, and paper methodology attribution all key off
`platform_class`.

---

## Two-Axis Model

Platform identity has two independent axes:

**`platform_class`** identifies the hardware ISA, vendor, and energy stack.
It is determined once at install time and written to `config/hw_config.json`.
It drives which platform folder installs system dependencies, which seed
migrations load, and which readers the factory instantiates.

**`execution_context`** identifies the virtualization layer the OS is running
inside. It is independent of `platform_class`. A `linux_arm` machine can be
`bare_metal`, `kvm`, `container`, or `unknown`. This matters for measurement
because KVM guests cannot read host RAPL counters even if the host is Intel x86.

`cloud_provider` is a third field but it is metadata only. It does not drive
any measurement or install decision.

---

## Detector Classes

`detect_hardware.py` implements a factory pattern. `PlatformDetector.for_current_platform()`
probes hardware signals and returns the appropriate detector instance.

| Detector Class | `platform_class` | Primary Signals |
|---|---|---|
| `AppleSiliconDetector` | `apple_silicon` | Darwin + arm64 + IOKit power plane readable |
| `IntelMacDetector` | `intel_mac` | Darwin + x86_64 |
| `IntelLinuxDetector` | `intel_x86` | Linux + x86_64 + GenuineIntel in /proc/cpuinfo |
| `AMDLinuxDetector` | `amd_x86` | Linux + x86_64 + AuthenticAMD in /proc/cpuinfo |
| `NVIDIAGraceDetector` | `nvidia_grace` | Linux + aarch64 + GB10 GPU + NVIDIA DMI tags |
| `GenericARMLinuxDetector` | `linux_arm` | Linux + aarch64 + not Grace |
| `RISCVLinuxDetector` | `linux_riscv` | Linux + riscv64 |
| fallback | `linux_x86_unknown` | Linux + x86_64 + unknown vendor |

Detection uses DMI table entries, `/proc/cpuinfo` vendor strings, GPU presence
via `nvidia-smi`, and product name strings from `/sys/class/dmi/id/`. It does
not use `uname` output alone.

---

## Execution Context Detection

`execution_context.py` detects the virtualization layer through a hierarchy
of signals, preserving evidence at each step:

```
1. systemd-detect-virt        — most reliable when systemd is present
2. DMI product name           — "Standard PC", "KVM", "VMware", etc.
3. Container markers          — /.dockerenv, /run/container_type, cgroup v2
4. /proc/version              — kernel build string sometimes reveals hypervisor
5. unknown                    — default when no signal is conclusive
```

The safety rule: `unknown` maps to soft severity, never to `bare_metal`
assumption. Assuming bare metal on a VM causes RAPL reads to silently return
zeros or wrong values, which corrupts the measurement record without any error.

---

## Capability Profile

After detection, each platform builds a `capability_profile`: a dictionary
of 9 boolean capabilities derived from real hardware reads.

**Governing invariant:**

> A capability is asserted only when A-LEMS can positively establish that the
> underlying data source is present AND readable. Each check opens the file,
> reads bytes, and parses a value. `os.path.exists()` alone is never sufficient.

This invariant separates `capability_profile` from `tool_availability`.
`tool_availability` records whether `perf`, `turbostat`, `dcgmi`, and
`nvidia-smi` are installed. A tool being installed does not mean the hardware
counter it reads is accessible. On a system where RAPL sysfs paths exist but
are permission-denied, `tool_availability` shows `perf: true` while
`capability_profile` shows `energy_rapl: false`. The capability profile governs
measurement decisions. Tool availability governs install hints only.

| Capability | What the check reads |
|---|---|
| `energy_rapl` | `/sys/class/powercap/intel-rapl*/energy_uj` — parses an integer |
| `energy_spbm` | `/sys/class/hwmon/hwmon*/energy*_input` on SPBM hwmon — parses an integer |
| `energy_iokit` | IOKit power plane via `ioreg` — confirms AppleARMIODevice or IOPMPowerSource |
| `energy_dcgm` | DCGM GPU telemetry channel — socket connection to nvidia-dcgm daemon |
| `compute_arm_pmu` | `/sys/bus/event_source/devices/armv8_pmuv3/` — sysfs presence + perf_event_open |
| `compute_rapl_pmu` | perf_event_open on RAPL PMU event — actual read |
| `compute_kperf` | `kperf` framework availability on Darwin — dynamic link check |
| `cpu_idle_states` | `/sys/devices/system/cpu/cpu0/cpuidle/` — directory exists and has state entries |
| `msr_readable` | `/dev/cpu/0/msr` — open + read at MSR address 0x10 (TSC) |

---

## Measurement Classification

`classify_measurement()` takes the `capability_profile` and `platform_class`
and returns two independent tiers:

**`compute_measurement`** — how CPU performance counters are read:
- `direct` — ARM PMU sysfs or RAPL PMU perf_event readable
- `estimated` — `/proc/stat` ticks used as proxy
- `unavailable` — no counter accessible

**`energy_measurement`** — how energy is read:
- `direct` — RAPL, SPBM, or IOKit counter readable right now
- `modeled` — no hardware energy counter; `energy_uj = 0` recorded with explicit tier
- `unavailable` — observation-only platform

Both tiers are stored in every run record and in the paper methodology
section. `energy_uj = 0` with `energy_measurement = modeled` is not an
error. It is an honest representation of what is measurable on that platform.

---

## Output: hw_config.json

`detect_hardware.py` writes `config/hw_config.json` after detection. This
file is the single source of truth for platform identity across all A-LEMS
tools. It is gitignored because it is machine-specific.

Key fields:

```json
{
  "hw_config_version": 4,
  "platform_class": "nvidia_grace",
  "execution_context": "bare_metal",
  "cloud_provider": null,
  "capability_profile": {
    "energy_rapl": false,
    "energy_spbm": true,
    "energy_dcgm": true,
    "compute_arm_pmu": true
  },
  "compute_measurement": "direct",
  "energy_measurement": "direct"
}
```

`hw_config_version` is incremented when the output contract changes. Any
consumer that checks this field can detect a stale config from a previous
install and re-run detection.

---

## Two-Pass Detection

The install system runs detection twice:

**Pass 0 (Step 0 of install.sh):** fast probe before venv or dependencies
exist. Uses `--stdout` flag so no file is written. Purpose: identify
`platform_class` so the installer knows which system packages to install first.

**Pass 1 (Step 4 of install.sh):** full detection after permissions are set
by `fix_permissions.sh`. At this point RAPL sysfs, MSR device, and perf_event
are accessible. The capability profile is complete and accurate. Writes
`hw_config.json`.

The two-pass design ensures that permission-gated capabilities like RAPL and
MSR are detected with correct access, not incorrectly reported as unavailable
because detection ran before `sudo fix_permissions.sh`.

---

## Adding a New Platform

See [Adding a Platform](../developer/adding-a-platform.md) for the four-step
process. Changes are confined to `detect_hardware.py` and the platform
folder in `scripts/platforms/`. No other file requires modification.
