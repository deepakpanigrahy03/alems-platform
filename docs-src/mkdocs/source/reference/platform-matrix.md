# Platform Matrix

A-LEMS supports 8 platform classes across 4 ISAs. Platform class is the
single identifier that drives install logic, reader selection, seed data,
and measurement tier assignment across the entire system.

---

## Platform Classes

| Platform Class | ISA | Hardware Examples | CPU Vendor | Energy Stack |
|---|---|---|---|---|
| `nvidia_grace` | aarch64 | GN100, DGX Spark | NVIDIA | SPBM + DCGM + ARM PMU |
| `intel_x86` | x86_64 | Any Intel bare metal | GenuineIntel | RAPL + MSR + turbostat |
| `amd_x86` | x86_64 | Any AMD bare metal | AuthenticAMD | RAPL + MSR |
| `apple_silicon` | arm64 | M1 / M2 / M3 / M4 Mac | Apple | IOKit + powermetrics |
| `linux_arm` | aarch64 | Graviton, RPi, KVM VMs | various | ARM PMU |
| `linux_x86_unknown` | x86_64 | VMs, Hygon, unknown x86 | unknown | RAPL if exposed |
| `intel_mac` | x86_64 | Pre-2020 Intel Mac | GenuineIntel | observation only |
| `linux_riscv` | riscv64 | SiFive, StarFive | various | observation only |

---

## Measurement Tiers

| Platform Class | Energy Measurement | Compute Measurement | energy_uj in DB |
|---|---|---|---|
| `nvidia_grace` | Direct (SPBM) or Modeled (Secure Boot) | Direct (ARM PMU) | non-zero or 0 |
| `intel_x86` | Direct (RAPL) | Direct (RAPL + PMU) | non-zero |
| `amd_x86` | Direct (RAPL) | Direct (RAPL + PMU) | non-zero |
| `apple_silicon` | Direct (IOKit) | Direct (kperf PMU) | non-zero |
| `linux_arm` | Modeled | Direct (ARM PMU) | 0 |
| `linux_x86_unknown` | Modeled | Estimated | 0 |
| `intel_mac` | Unavailable | Estimated | 0 |
| `linux_riscv` | Unavailable | Estimated | 0 |

Direct means a hardware counter is read. Modeled means no hardware energy
counter is accessible — `energy_uj = 0` is recorded with an explicit
measurement tier. Unavailable means CPU time only is recorded.

---

## Detection Signals

Each platform class is detected by hardware signals, not by OS or
architecture strings alone.

| Platform Class | Primary Detection Signal |
|---|---|
| `nvidia_grace` | Linux + aarch64 + GB10 GPU in nvidia-smi + NVIDIA DMI tags |
| `intel_x86` | Linux + x86_64 + GenuineIntel in /proc/cpuinfo |
| `amd_x86` | Linux + x86_64 + AuthenticAMD in /proc/cpuinfo |
| `apple_silicon` | Darwin + arm64 + IOKit power plane readable |
| `linux_arm` | Linux + aarch64 + not Grace |
| `linux_x86_unknown` | Linux + x86_64 + unknown vendor |
| `intel_mac` | Darwin + x86_64 |
| `linux_riscv` | Linux + riscv64 |

---

## Capability Profile per Platform

| Capability | nvidia_grace | intel_x86 | amd_x86 | apple_silicon | linux_arm |
|---|---|---|---|---|---|
| `energy_rapl` | ✗ | ✓ | ✓ | ✗ | ✗ |
| `energy_spbm` | ✓ (if Secure Boot off) | ✗ | ✗ | ✗ | ✗ |
| `energy_iokit` | ✗ | ✗ | ✗ | ✓ | ✗ |
| `energy_dcgm` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `compute_arm_pmu` | ✓ | ✗ | ✗ | ✗ | ✓ |
| `compute_rapl_pmu` | ✗ | ✓ | ✓ | ✗ | ✗ |
| `compute_kperf` | ✗ | ✗ | ✗ | ✓ | ✗ |
| `msr_readable` | ✗ | ✓ | ✓ | ✗ | ✗ |
| `cpu_idle_states` | ✓ | ✓ | ✓ | ✗ | ✓ |

---

## Platform Folder Structure

Each platform class has a dedicated folder in `scripts/platforms/`:

```
scripts/platforms/
    nvidia_grace/      provision.sh, verify.sh, seed SQL
    intel_x86/         provision.sh, verify.sh
    amd_x86/           provision.sh, verify.sh
    apple_silicon/     provision.sh, verify.sh
    linux_arm/         provision.sh, verify.sh
    linux_x86_unknown/ provision.sh, verify.sh
    intel_mac/         stub
    linux_riscv/       stub
```

The installer reads `platform_class` from `hw_config.json` and runs the
corresponding platform folder's scripts. Adding a new platform requires
only adding a detector class and a platform folder. No other file changes.

---

## Verifying Your Platform

```bash
# Show detected platform class and measurement tiers
alems dev status

# Show full hw_config.json
cat config/hw_config.json

# Re-run detection
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/detect_hardware.py --stdout
```
