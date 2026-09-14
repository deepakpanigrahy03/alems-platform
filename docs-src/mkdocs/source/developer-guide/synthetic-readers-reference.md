# Synthetic Reader Reference

## Overview

Three synthetic readers implement the full reader ABC contracts for the
synthetic platform. Each reader is registered in the global registry
alongside real readers on every machine. On real hardware, `can_handle()`
returns False and they are never selected. When
`ALEMS_PLATFORM_OVERRIDE=synthetic` is set, they are selected exclusively.

This document is the technical reference for each reader's interface,
return values, configuration, and extension points. For the conceptual
overview of why the synthetic platform exists and how it works, see
`synthetic-platform.md`.

---

## SyntheticEnergyReader

**File:** `core/readers/synthetic_energy_reader.py`
**Inherits:** `EnergyReaderABC`
**Registry family:** `energy`
**METHOD_ID:** `synthetic_energy`
**PRIORITY:** 100

### Purpose

Replaces hardware energy counter reads (`read_normalized()`) with
deterministic values from a YAML fixture. Supports three modes: constant,
linear, and replay. See `synthetic-platform.md` for mode details.

### Return Values (constant mode, default)

| Field | Value | Unit | Notes |
|---|---|---|---|
| `pkg_uj` | 1,000,000 | µJ | Total package energy |
| `cpu_uj` | 600,000 | µJ | CPU cores (60% of package) |
| `gpu_uj` | None | — | No GPU domain on synthetic platform |
| `dram_uj` | 200,000 | µJ | DRAM domain |
| `ts_ns` | `time.monotonic_ns()` | ns | Live timestamp (not fixed) |

### Domains

`get_domains()` returns `["package-0", "core", "dram"]`.

This mirrors RAPL domain names deliberately. The rest of the pipeline
(EnergyCollector, domain mapping, DB writes) handles synthetic data
identically to real x86 RAPL data because the domain names match.

### Measurement Schema

`get_measurement_schema()` returns a `MeasurementSchema` with:

| Field | Value |
|---|---|
| `source` | `"SYNTHETIC"` |
| `sampling_hz` | 100 |
| `counter_width_bits` | 64 |
| `domains` | PACKAGE (root), CORE (child of PACKAGE), DRAM (root) |

`counter_width_bits=64` means `EnergyCollector` never applies wraparound
correction to synthetic values. This is correct — fixture values never
overflow a 64-bit counter.

### METHOD_CONFIDENCE = 0.0

Synthetic values are not physically measured. `METHOD_CONFIDENCE = 0.0`
ensures analysis queries filtering on confidence correctly exclude
synthetic runs. `is_available()` returns True because the reader can
always function — no hardware is needed.

### Configuration

Full configuration reference:

```yaml
# config/app_settings.yaml
plugins:
  synthetic:
    mode: constant           # constant | linear | replay

    # Constant mode
    package_uj: 1000000
    core_uj:     600000
    dram_uj:     200000

    # Linear mode
    increment_uj: 50000      # added per call on top of start values

    # Replay mode
    csv_path: data/fixtures/synthetic_energy_replay.csv
```

### Interface Summary

| Method | Returns | Notes |
|---|---|---|
| `is_available()` | `True` | Always |
| `get_name()` | `"SyntheticEnergyReader(mode=...)"` | Includes mode |
| `read_normalized()` | `NormalizedEnergyReading` | From fixture |
| `read_energy_uj()` | `dict` | Same values, dict form |
| `get_domains()` | `["package-0", "core", "dram"]` | Fixed |
| `get_measurement_schema()` | `MeasurementSchema` | SYNTHETIC source |
| `read_gpu_msr()` | `None` | No GPU domain |
| `get_method_id()` | `"synthetic_energy"` | |
| `get_confidence()` | `0.0` | Not physically measured |

---

## SyntheticCPUReader

**File:** `core/readers/synthetic_cpu_reader.py`
**Inherits:** `CPUReaderABC`
**Registry family:** `cpu`
**METHOD_ID:** `synthetic_cpu`
**PRIORITY:** 100

### Purpose

Replaces hardware PMU counter reads with fixed, realistic values.
Enables `cpu_samples` table to be populated during synthetic runs.

### Return Values

| Method | Value | Unit | Rationale |
|---|---|---|---|
| `read_instructions()` | 1,000,000 | count | Round number, realistic for short inference |
| `read_cycles()` | 500,000 | count | Implies IPC of 2.0 |
| `read_ipc()` | 2.0 | ratio | Realistic for modern ARM/x86 |
| `read_frequency_mhz()` | 2400.0 | MHz | 2.4 GHz nominal |

**Why these values:** 2.0 IPC is achievable and realistic on NVIDIA Grace
(Neoverse V2) and modern x86. 2400 MHz is a common nominal frequency on
both platforms. The values are round enough to assert exactly in tests
but realistic enough to not produce nonsensical derived metrics.

### Interface Summary

| Method | Returns | Notes |
|---|---|---|
| `is_available()` | `True` | Always |
| `get_name()` | `"SyntheticCPUReader"` | |
| `read_instructions()` | `1_000_000` | Fixed |
| `read_cycles()` | `500_000` | Fixed |
| `read_ipc()` | `2.0` | Fixed |
| `read_frequency_mhz()` | `2400.0` | Fixed |
| `start_process_measurement(pid)` | None | No-op |
| `stop_process_measurement()` | `dict` | Zeros |

---

## SyntheticThermalReader

**File:** `core/readers/synthetic_thermal_reader.py`
**Inherits:** `ThermalReaderABC`
**Registry family:** `thermal`
**METHOD_ID:** `synthetic_thermal`
**PRIORITY:** 100

### Purpose

Replaces hardware thermal sensor reads with a fixed, realistic temperature.
Enables `thermal_samples_v2` table to be populated during synthetic runs.

### Return Values

| Method | Returns | Unit | Rationale |
|---|---|---|---|
| `read_all_thermal()` | `{"cpu_package": 45.0}` | °C | Above ambient, below throttle |

**Why 45°C:** This value is:
- Above ambient (~20°C) — confirms the reader is returning data, not zeros
- Below thermal throttle threshold (85°C typical) — does not trigger any
  throttle-detection code paths that would alter measurement behavior
- Realistic for light-to-moderate CPU load on ARM and x86 platforms
- Round enough to assert exactly in tests

### Interface Summary

| Method | Returns | Notes |
|---|---|---|
| `is_available()` | `True` | Always |
| `get_name()` | `"SyntheticThermalReader"` | |
| `read_all_thermal()` | `{"cpu_package": 45.0}` | Fixed dict |

---

## Registry Behavior on Different Platforms

The table below shows which reader is selected per family on each platform.

### With ALEMS_PLATFORM_OVERRIDE=synthetic

| Family | Selected Reader | Reason |
|---|---|---|
| energy | SyntheticEnergyReader | platform_class=="synthetic" |
| cpu | SyntheticCPUReader | platform_class=="synthetic" |
| thermal | SyntheticThermalReader | platform_class=="synthetic" |
| turbostat | DummyTurbostatReader (fallback) | No synthetic turbostat in Part B |
| msr | DummyMSRReader (fallback) | No synthetic MSR in Part B |
| scheduler | SchedulerMonitor or DummySchedulerMonitor | OS-dependent |
| disk | Platform disk reader or Fallback | OS-dependent |

### Without ALEMS_PLATFORM_OVERRIDE (real hardware)

| Platform | energy | cpu | thermal |
|---|---|---|---|
| NVIDIA Grace GB10 | SPBMEnergyReader | ARMPMUReader | ARMThermalReader |
| Intel Linux | RAPLReader | PerfReader | SensorReader |
| AMD Linux | RAPLReader | PerfReader | SensorReader |
| Apple Silicon | IOKitPowerReader | KPerfPMUReader | IOKitThermalReader |

Synthetic readers never appear in the "without override" column. This is
structural, not conventional — `can_handle()` always returns False on
real hardware.

---

## Writing Tests with Synthetic Readers

### Pattern 1: Assert exact energy value

```python
import os
import subprocess

def test_synthetic_energy_in_db(tmp_db):
    os.environ["ALEMS_PLATFORM_OVERRIDE"] = "synthetic"
    # run experiment
    result = run_experiment(task="gsm8k_linear", provider="llama_cpp", db=tmp_db)
    assert result["energy_uj"] == 1_000_000
    del os.environ["ALEMS_PLATFORM_OVERRIDE"]
```

### Pattern 2: Assert registry selects synthetic reader

```python
def test_registry_selects_synthetic():
    os.environ["ALEMS_PLATFORM_OVERRIDE"] = "synthetic"
    from core.readers.bootstrap import register_all_readers, energy_registry
    from core.utils.platform import get_platform_capabilities
    register_all_readers()
    caps = get_platform_capabilities(force_refresh=True)
    cls = energy_registry.select(caps)
    assert cls.__name__ == "SyntheticEnergyReader"
    del os.environ["ALEMS_PLATFORM_OVERRIDE"]
```

### Pattern 3: Test delta computation with linear mode

```python
def test_energy_delta_computation():
    reader = SyntheticEnergyReader({"plugins": {"synthetic": {
        "mode": "linear",
        "package_uj": 1_000_000,
        "increment_uj": 50_000,
    }}})
    r1 = reader.read_normalized()   # call 1: pkg=1_000_000
    r2 = reader.read_normalized()   # call 2: pkg=1_050_000
    delta = r2.delta(r1)
    assert delta.pkg_uj == 50_000
```

---

## Known Limitations and Future Work

**No synthetic turbostat reader in Part B.**
The `turbostat` family has no synthetic implementation. On a synthetic
platform session, `DummyTurbostatReader` is returned. This means
`turbostat_reader` and related columns in the DB are NULL for synthetic
runs. This is acceptable for energy pipeline testing but means C-state
and frequency data cannot be tested synthetically in Part B.
Future: `SyntheticTurbostatReader` returning fixed DataFrames.

**No synthetic MSR, disk, scheduler, or NIC readers in Part B.**
Same rationale — not needed for energy pipeline CI testing.
Future: full synthetic coverage for all reader families.

**RAPLReader can_handle() overlap on Linux x86.**
On a Linux x86 developer machine with `ALEMS_PLATFORM_OVERRIDE=synthetic`,
both `RAPLReader` and `SyntheticEnergyReader` pass `can_handle()`.
Both are PRIORITY=100. This causes `ConfigurationError` (tie).
Workaround: run synthetic tests on GN100, or add
`caps.platform_class != "synthetic"` guard to `RAPLReader.can_handle()`.
This guard will be added in a follow-up fix.

**Synthetic runs not DB-flagged.**
The `runs` table has no `run_type` or `is_synthetic` column.
Identify synthetic runs via `method_id = 'synthetic_energy'` in
`measurement_method_registry`. A future schema version may add an
explicit flag.
