# Energy Tiers

A-LEMS classifies every measurement into one of three energy tiers and
one of three compute tiers. These tiers are determined at install time
by reading actual hardware counters — not by guessing from platform names.
Both tiers are stored in every run record and in `hw_config.json`.

---

## Why Tiers Exist

Different hardware exposes different measurement interfaces. An NVIDIA
Grace SoC exposes SPBM hwmon rails. An Intel bare metal machine exposes
RAPL sysfs counters. A KVM virtual machine exposes neither. A-LEMS
records what is actually measurable rather than substituting estimates.

A measurement with `energy_uj = 0` and `energy_measurement = modeled`
is scientifically honest. A measurement with an estimated value and no
indication that it is estimated is not.

---

## Energy Measurement Tiers

### Direct

A hardware counter is read. The value comes from a physical sensor or
accumulator register, not from a model or proxy.

| Platform | Counter | Interface |
|---|---|---|
| `intel_x86`, `amd_x86` | RAPL package energy | `/sys/class/powercap/intel-rapl*/energy_uj` |
| `nvidia_grace` (Secure Boot off) | SPBM hwmon rails | `/sys/class/hwmon/hwmon*/energy*_input` |
| `apple_silicon` | IOKit power plane | `ioreg` via IOKit framework |
| GPU (DCGM) | DCGM field 156 | `nvidia-dcgm` daemon |

Direct energy measurements are suitable for paper citations without
qualification. The governing invariant: A-LEMS asserts a capability
only when the underlying counter is present AND readable — a file
existing at the sysfs path is not sufficient; the value must parse
as a non-negative integer.

### Modeled

No hardware energy counter is accessible. `energy_uj = 0` is recorded
in the runs table with `energy_measurement = modeled`. This applies to:

- KVM virtual machines (RAPL is not exposed to guests)
- ARM Linux platforms without SPBM (Graviton, Raspberry Pi)
- NVIDIA Grace with Secure Boot enabled (SPBM hwmon requires unsigned kernel module)
- x86 VMs and unknown x86 platforms where RAPL is not exposed

A modeled measurement is not an error. Papers using modeled platforms
must state explicitly that energy values are not available and that
`energy_uj = 0` reflects a measurement limitation, not zero energy consumption.

### Unavailable

The platform records CPU time only. No energy counter and no proxy model.
Applies to `intel_mac` (pre-2020 Intel Mac) and `linux_riscv`.

---

## Compute Measurement Tiers

### Direct

Hardware performance counters are read via `perf_event_open` or ARM PMU
sysfs. Provides instructions, cycles, IPC, and cache miss counts.

| Platform | Interface |
|---|---|
| `intel_x86`, `amd_x86` | RAPL PMU + Linux perf |
| `nvidia_grace`, `linux_arm` | ARM PMU sysfs |
| `apple_silicon` | kperf private framework |

### Estimated

CPU time is derived from `/proc/stat` tick counts. Instructions and
cycles are not available. Applies to `linux_x86_unknown` VMs and
unknown x86 platforms.

### Unavailable

No compute counter or proxy is accessible. Applies to `intel_mac`
and `linux_riscv`.

---

## Tier Classification Logic

`classify_measurement()` in `scripts/detection/capability_profile.py`
determines both tiers from the capability profile. The logic runs two
parallel tracks:

**Energy track:** checks `energy_rapl` → `energy_spbm` → `energy_iokit`
in order. First readable counter wins → direct. If compute is available
but no energy counter → modeled. Otherwise → unavailable.

**Compute track:** checks `compute_arm_pmu` → `compute_rapl_pmu` →
`compute_kperf` in order. First readable counter wins → direct.
Falls back to `/proc/stat` → estimated. Otherwise → unavailable.

---

## Checking Your Tiers

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

# Regenerate hw_config.json with current hardware state
python3 scripts/detect_hardware.py

# Show tiers and capability profile
python3 -c "
import json
with open('config/hw_config.json') as f:
    cfg = json.load(f)
print('platform_class:     ', cfg.get('platform_class'))
print('energy_measurement: ', cfg.get('energy_measurement'))
print('compute_measurement:', cfg.get('compute_measurement'))
print('capability_profile:')
for k, v in cfg.get('capability_profile', {}).items():
    print(f'  {k}: {v}')
"
```

---

## Tiers in the Database

Both tiers are stored in every run record:

```sql
SELECT run_id, workflow_type,
       total_energy_uj,
       energy_measurement_mode
FROM runs
ORDER BY run_id DESC
LIMIT 5;
```

On direct energy platforms, `total_energy_uj` is non-zero.
On modeled platforms, `total_energy_uj = 0` and
`energy_measurement_mode = 'modeled'`.

---

## Citing Tiers in Papers

A paper methodology section must state the measurement tier for each
platform used. Example:

**Direct energy platform (intel_x86):** "Energy was measured using
Intel RAPL package energy counters via the Linux powercap sysfs
interface at 100 Hz. Values represent cumulative microjoule readings
baseline-subtracted using a 90-second idle measurement."

**Modeled platform (linux_arm):** "This platform does not expose
hardware energy counters. Energy values are recorded as zero in the
dataset. Compute metrics (instructions, cycles, IPC) are available
via ARM PMU sysfs."
