# Energy Reader Methodology

This document describes the measurement methodology for all energy reader
implementations in A-LEMS. Each reader implements `EnergyReaderABC` and
is selected automatically by `ReaderFactory` based on platform capabilities
detected at startup.

---

## RAPL Energy Measurement

### Overview

Intel RAPL (Running Average Power Limit) is a hardware interface that exposes
cumulative energy consumption counters via Model Specific Registers (MSRs)
and the Linux sysfs powercap interface. A-LEMS reads these counters at
100Hz to produce high-resolution energy time series.

### Hardware Interface

RAPL exposes energy counters through the Linux kernel's powercap subsystem:

```
/sys/class/powercap/intel-rapl/
    intel-rapl:0/           ← Package 0
        energy_uj           ← Cumulative package energy in µJ
        intel-rapl:0:0/     ← Core power plane (PP0)
            energy_uj
        intel-rapl:0:1/     ← Uncore power plane (PP1)
            energy_uj
    intel-rapl:1/           ← Package 1 (dual-socket systems)
```

The primary register is `MSR_PKG_ENERGY_STATUS` (MSR address `0x611`),
which accumulates energy consumed by the entire CPU package including
cores, cache, memory controller, and integrated GPU.

### Energy Calculation

Energy is computed as a delta between two counter readings:

$$E_{pkg} = R_{end} - R_{start}$$

Where:
- $E_{pkg}$ = Package energy consumed in µJ
- $R_{end}$ = RAPL counter value at measurement end
- $R_{start}$ = RAPL counter value at measurement start

**Counter wrap-around handling:**

RAPL counters are 32-bit and wrap at approximately $2^{32}$ µJ (≈70 minutes
at 1000W TDP). A-LEMS detects and corrects wrap-around:

$$E_{pkg} = \begin{cases} R_{end} - R_{start} & \text{if } R_{end} \geq R_{start} \\ (2^{32}-1 - R_{start}) + R_{end} & \text{wrap-around} \end{cases}$$

### Sampling Architecture

A-LEMS samples RAPL at 100Hz using a dedicated high-frequency sampling thread:

```
EnergyEngine._sampling_loop()
    ├── t=0ms:   read start counter
    ├── t=10ms:  read end counter → compute delta → enqueue
    ├── t=10ms:  read start counter
    ├── t=20ms:  read end counter → compute delta → enqueue
    └── ...
```

Each sample records:
- `sample_start_ns` — nanosecond timestamp at counter read start
- `sample_end_ns` — nanosecond timestamp at counter read end
- `pkg_start_uj`, `pkg_end_uj` — raw counter values
- `interval_ns` — actual elapsed time between reads

### Domains

A-LEMS captures four energy domains when available:

| Domain | MSR | Description |
|--------|-----|-------------|
| `package-0` | `0x611` | Entire CPU package (primary metric) |
| `core` | `0x639` | CPU cores only (excludes uncore) |
| `uncore` | `0x641` | Cache, memory controller, ring bus |
| `dram` | `0x61B` | DRAM controller (server CPUs only) |

### Platform Availability

| Platform | Available | Confidence |
|----------|-----------|------------|
| Linux x86_64 bare metal | ✅ Yes | 1.0 |
| Linux x86_64 VM (RAPL passthrough) | ✅ Yes | 1.0 |
| Linux aarch64 (ARM VM) | ❌ No | 0.0 |
| macOS | ❌ No | 0.0 |

### Provenance

- **Provenance**: `MEASURED`
- **Method ID**: `rapl_msr_pkg_energy`
- **Confidence**: `1.0`
- **Fallback**: `ml_energy_estimator`

### References

- Intel Corporation. *Intel 64 and IA-32 Architectures Software Developer
  Manual*, Volume 3B, Section 14.9. 2023.
- Sangha, A. et al. *A-LEMS: Agent vs Linear Energy Measurement Platform*.
  arXiv:2509.09991, 2025.

---

---
**Method ID:** iokit_power_reader
**Schema version:** runs (152 columns, confirmed identical across NVIDIA Grace GB10, Intel i7-1165G7, and Apple M1 Pro platforms)
**Platforms verified:** Apple M1 Pro (arm64)
**Status:** PRODUCTION
**Last updated:** 2026-07-06
---

## IOKit Power Reader

### Overview
On macOS, hardware power sensors are accessible via Apple's IOKit framework.
A-LEMS reads instantaneous power (watts) from IOKit HID services through
the powermetrics utility and integrates over time to produce cumulative
energy values:

$$E_{pkg} = \sum_{i} P_i \cdot \Delta t_i \times 10^6$$

Where:
- $P_i$ = Instantaneous power in watts from IOKit sensor
- $\Delta t_i$ = Elapsed seconds since last read
- Result in µJ (multiply by $10^6$)

### Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| Apple M1 Pro | arm64 | powermetrics cpu_power/gpu_power/ane_power samplers | CPU_APPLE / GPU_APPLE | 0.85 | VERIFIED |
| Intel Mac | x86_64 | SMC via IOKit HID (same mechanism, untested) | CPU_APPLE / GPU_APPLE | TBD | PLANNED |

### Schema

| Column | Table | Type | Semantics |
|--------|-------|------|-----------|
| core_energy_uj | runs | INTEGER | Cumulative CPU energy, µJ, per run |
| gpu_total_energy_uj | runs | INTEGER | Cumulative GPU energy, µJ, secondary channel on this platform (primary is IOKit GPU backend, 15-B) |
| pkg_energy_uj | runs | INTEGER | Always NULL on this platform, no separate package rail on Apple's unified memory architecture |
| dram_energy_uj | runs | INTEGER | Always NULL on this platform, same reason |

### Method Provenance

method_id: `iokit_power_reader`
provenance: MEASURED
layer: silicon
confidence: 0.85

**Confidence 0.85**: powermetrics reports instantaneous power at a fixed
sampling interval (500ms in the current implementation), not a cumulative
hardware energy counter. Energy is derived via trapezoidal integration
between successive samples. This introduces sensitivity to power
transients shorter than the sampling window, most relevant for sub-second
inference calls. To reach 1.0 would require a true cumulative hardware
energy counter, which IOKit does not expose on this platform. Under
sustained inference runs (multiple seconds or longer), integration error
is expected to be small; this has not yet been quantified against a
higher-frequency reference measurement.

### Query Reference

**Real energy for a specific run on Apple Silicon** — answers: how much
CPU and GPU energy did this run consume?
Applies to: Apple M1 Pro (arm64) only.
Expected output: one row, core_energy_uj and gpu_total_energy_uj in
microjoules, pkg_energy_uj and dram_energy_uj NULL (architectural, not
a gap).

```sql
SELECT run_id, core_energy_uj, gpu_total_energy_uj, pkg_energy_uj, dram_energy_uj
FROM runs
WHERE run_id = <your-run-id>;
```

Note: this query has not yet been re-verified against the current schema
per PDS-4 following the 2026-07-06 schema changes (14 columns added to
runs). Re-run and confirm before treating this section as fully compliant.

### Verification

```bash
# Confirm the reader dispatches correctly
python3 -c "
from core.readers.factory import ReaderFactory
from core.utils.platform import get_platform_capabilities
caps = get_platform_capabilities()
reader = ReaderFactory.get_energy_reader({}, caps)
print(type(reader).__name__)
"
# Expected: IOKitPowerReader

# Confirm real energy accumulates under load
python3 -c "
from core.readers.darwin.iokit_power_reader import IOKitPowerReader
import time
r = IOKitPowerReader({})
time.sleep(3)
print(r.read_energy_uj())
"
# Expected: {'cpu': <positive integer>, 'gpu': <non-negative integer>}
```

### Known Limitations

- **No package-level energy**: Apple's unified memory architecture has
  no equivalent to a package rail. `pkg_energy_uj` is NULL.
  Workaround: None — accept NULL.
- **No DRAM energy**: same architectural reason. `dram_energy_uj` is NULL.
  Workaround: None — accept NULL.
- **500ms sampling window**: sub-second LLM calls may have their true
  power profile smoothed by the integration window.
  Workaround: None at the powermetrics layer; a shorter sampling interval
  reduces but does not eliminate this, at the cost of higher overhead.
  Not yet attempted.
- **Confidence not independently validated against a reference meter**:
  0.85 reflects the known integration methodology limitation, not a
  measured error bound against ground truth.
  Workaround: None currently; would require an external power meter.

---
**Method ID:** iokit_thermal_reader
**Schema version:** runs (152 columns, same cross-platform baseline as above)
**Platforms verified:** Apple M1 Pro (arm64)
**Status:** PRODUCTION
**Last updated:** 2026-07-06
---

## IOKit Thermal Reader

### Overview
Apple Silicon thermal data draws from three independent sources: die
temperature, battery temperature, and a categorical thermal pressure
level. Package temperature is computed as the maximum across all
available die probes:

$$T_{package} = \max(T_{die,0}, T_{die,1}, \ldots, T_{die,N})$$

### Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| Apple M1 Pro | arm64 | Vendored sensors.m via IOHIDEventSystemClient | CPU_APPLE (package proxy) | 0.75 | VERIFIED |

### Schema

| Column | Table | Type | Semantics |
|--------|-------|------|-----------|
| package_temp_celsius | runs | REAL | Max of all live die probe readings for the run |

Battery temperature and categorical pressure level are captured at
measurement time but are not currently persisted as separate `runs`
columns; they are available via `get_pressure_level()` for logging and
provenance cross-reference only, not stored per-run in the schema today.
This is a real, current gap, not a design decision to hide the data;
adding dedicated columns for these is unimplemented future work.

### Method Provenance

method_id: `iokit_thermal_reader`
provenance: MEASURED
layer: silicon
confidence: 0.75

**Hardware interface, real mechanism, not the Python wrapper alone**: die
temperature is NOT read directly by A-LEMS's own Python code. A vendored,
compiled Objective-C helper (`sensors.m`, author Koan-Sin Tan, BSD
3-Clause license, source at github.com/freedomtan/sensors_cmdline,
vendored at `core/readers/darwin/vendor/sensors.m`) performs the actual
sensor enumeration via Apple's public IOHIDEventSystemClient framework,
using constants published in Apple's own open source IOHIDFamily headers
(AppleHIDUsageTables.h, IOHIDEventTypes.h). The Python reader invokes
this binary as a subprocess and parses its text output. The registry's
`code_snapshot` column reflects only the Python wrapper file; the
vendored source at the path above is the actual measurement
implementation and is not embedded in `code_snapshot`.

Two sensor entries returned by the underlying enumeration are excluded
from aggregation, confirmed not to be live readings: one entry is fixed
at an identical value across every observed sample regardless of thermal
load (a calibration constant, not a temperature), and one entry has no
corresponding physical sensor and is fixed at zero.

**Confidence 0.75**: die temperature is real and responds to load (rose
monotonically under sustained multi-core saturation in testing), but the
exact physical mapping of each individual probe to a specific core
cluster or silicon region is not independently confirmed, only that the
aggregate maximum is a live, physically plausible signal. Battery
temperature and categorical pressure level are independent, real, lower
weight cross-reference signals, not the primary basis for this score.

### Query Reference

**Package temperature for a specific run** — answers: what was the peak
die temperature during this run?
Applies to: Apple M1 Pro (arm64) only.
Expected output: one row, package_temp_celsius as a real number, typically
in the 25-45°C range at light to moderate load based on observed testing.

```sql
SELECT run_id, package_temp_celsius
FROM runs
WHERE run_id = <your-run-id>;
```

Note: not yet re-verified against the current schema per PDS-4 following
the 2026-07-06 schema changes. Re-run and confirm before treating this
section as fully compliant.

### Verification

```bash
python3 -c "
from core.readers.darwin.iokit_thermal_reader import IOKitThermalReader
r = IOKitThermalReader({})
print(r.read_all_thermal())
print('pressure level:', r.get_pressure_level())
"
# Expected: {'package_temp_celsius': <real float>, 'battery_celsius': <real float>}
# pressure level: one of Nominal/Fair/Serious/Critical
```

### Known Limitations

- **No numeric CPU temperature via powermetrics itself**: the thermal
  pressure sampler only reports a four-level categorical value, not
  degrees. Die temperature comes from the separate vendored sensor path,
  not powermetrics.
  Workaround: use the vendored sensors.m die temperature reading as the
  primary numeric signal; treat pressure level as cross-reference only.
- **Per-probe physical mapping not independently confirmed**: it is not
  established which specific silicon region each `tdie` probe
  corresponds to.
  Workaround: None currently; aggregate maximum used as a conservative
  proxy for package temperature.
- **Battery and pressure signals not persisted per-run**: currently
  available only via method calls at measurement time, not stored as
  dedicated `runs` columns.
  Workaround: None currently; would require a schema addition, not yet
  implemented.
- **Third-party vendored code dependency**: die temperature measurement
  depends on a third-party compiled binary, not audited beyond the
  cross-validation described above.
  Workaround: None currently; BSD 3-Clause license and public framework
  usage reduce but do not eliminate this dependency risk.

### Platform Availability

| Platform | Available | Confidence |
|----------|-----------|------------|
| macOS Intel | ✅ Real hardware | 0.5 (stub) → 1.0 (Chunk 1.1) |
| macOS Apple Silicon | ✅ Real hardware | 0.5 (stub) → 1.0 (Chunk 1.1) |
| Linux | ❌ No | 0.0 |

### Provenance

- **Provenance**: `MEASURED`
- **Method ID**: `iokit_power_reader`
- **Confidence**: `0.5` (stub) → `1.0` (post Chunk 1.1)
- **Fallback**: `ml_energy_estimator`

### References

- Apple Inc. *IOKit Power Management Programming Guide*. 2022.
  https://developer.apple.com/documentation/iokit

---

## IOReport CPU Frequency (ioreport_cpufreq_v1)

---
**Method ID:** ioreport_cpufreq_v1
**Schema version:** No schema change (frequency_mhz column exists)
**Platforms verified:** Apple M1 Pro (arm64)
**Status:** PRODUCTION
**Last updated:** 2026-07
---

### Overview

Reads per-core DVFS residency counters from Apple's `libIOReport.dylib`
— the same library that `powermetrics` links against internally (confirmed
via `otool -L /usr/bin/powermetrics`). Computes the true wall-clock-weighted
average P-cluster frequency over any measurement window.

`powermetrics` reports HW-active-weighted frequency: averaged only over
active cycles, ignoring idle time. Under any real workload this always
reads near maximum (3036 MHz on M1 Pro) even when the CPU is mostly idle.
IOReport DVFS residency counters include idle time, giving the true
effective frequency that correlates correctly with measured energy.

No sudo required. No subprocess spawned. No compilation. Pure Python ctypes.

### Platform Coverage

| Platform | Architecture | Source | Confidence | Status |
|---|---|---|---|---|
| Apple M1 Pro | arm64/Darwin | IOReport DVFS residency (libIOReport.dylib) | 0.95 | VERIFIED |
| Intel i7-1165G7 | x86_64/Linux | turbostat (not applicable) | 0.92 | VERIFIED |
| NVIDIA Grace GB10 | aarch64/Linux | DummyTurbostatReader | N/A | NOT_SUPPORTED |

### Schema

No new columns. The existing `runs.frequency_mhz` column is populated by
this reader on Darwin. All other platforms continue to use their existing
readers unchanged.

### Method Provenance

| Field | Value |
|---|---|
| method_id | ioreport_cpufreq_v1 |
| provenance | MEASURED |
| layer | silicon |
| confidence | 0.95 |
| formula | f_weighted = sum(f_i * r_{i+1}) / sum(r_j for j=0..N) |

**Confidence Rationale (0.95):** IOReport DVFS residency counters are exact
hardware-level state accounting. The 0.05 deduction reflects that IOReport
is a private undocumented Apple API. Stable from macOS 11 (2020) through
macOS 16 (2026) but no formal API stability guarantee.

### Query Reference

```sql
SELECT run_id, frequency_mhz, total_energy_uj / 1e6 AS total_j
FROM runs
WHERE frequency_mhz IS NOT NULL AND frequency_mhz > 0
ORDER BY run_id DESC LIMIT 20;
```

### Verification

```bash
python3 scripts/tests/test_ioreport_standalone.py

python -m core.execution.tests.test_harness \
    --task-id gsm8k_basic --repetitions 1 --provider llama_cpp --save-db

DB=$(python3 -c "from scripts.tools.path_loader import get_alems_db_path; \
                 print(get_alems_db_path())")
sqlite3 "$DB" "SELECT run_id, frequency_mhz FROM runs ORDER BY run_id DESC LIMIT 2;"
```

### Known Limitations

- **Private API**: IOReport is undocumented. Three-tier fallback ensures
  A-LEMS never crashes if the API changes.
- **No per-sample time series**: IOReport provides interval aggregates only.
  `num_samples` is always 1.
- **Future chip compatibility**: Key `voltage-states5-sram` confirmed on M1
  through M4. Heuristic scan handles unknown chips automatically.

---

## Estimator (ML-Based Energy Estimation)

### Overview

On ARM VMs and platforms without RAPL access, A-LEMS uses a trained
XGBoost model to estimate package energy from observable system metrics:

$$\hat{E}_{pkg} = f_{\theta}(\text{cpu\_util}, \text{freq\_mhz}, \text{instructions}, \text{task\_type})$$

Where $f_{\theta}$ is a trained regression model with parameters $\theta$.

### Feature Vector

| Feature | Source | Unit |
|---------|--------|------|
| `cpu_util_pct` | `/proc/stat` | % |
| `freq_mhz` | turbostat | MHz |
| `instructions` | perf counters | count |
| `task_type` | experiment config | categorical |

### Current Status

**Stub implementation** — returns zeros pending model training
(planned for Chunk 7 when training data is available from UBUNTU2505
bare metal runs).

### Platform Availability

| Platform | Available | Confidence |
|----------|-----------|------------|
| Linux aarch64 (ARM VM) | Model pending | 0.0 (stub) → TBD (Chunk 7) |
| Any platform without RAPL | Model pending | 0.0 (stub) |

### Provenance

- **Provenance**: `INFERRED`
- **Method ID**: `ml_energy_estimator`
- **Confidence**: `0.0` (stub) → validated score (Chunk 7)
- **Fallback**: None (last resort)

### References

- Sangha, A. et al. *Data-Driven Energy Estimation for Virtual Servers*.
  arXiv:2509.09991, Section 4. 2025.

---

## Dummy Energy Reader

### Overview

Safe fallback for completely unsupported platforms (Windows, WSL, unknown OS).
Returns zero for all energy domains. Never raises exceptions — system
stability is guaranteed on any platform.

$$E_{pkg} = 0 \quad \text{(platform not supported)}$$

### Purpose

The dummy reader ensures A-LEMS never crashes due to missing hardware support.
All results from dummy reader runs are marked `LIMITED` with `confidence=0.0`
and should be excluded from any energy analysis.

### Platform Availability

| Platform | Available | Confidence |
|----------|-----------|------------|
| Windows | ✅ (returns zeros) | 0.0 |
| WSL | ✅ (returns zeros) | 0.0 |
| Unknown OS | ✅ (returns zeros) | 0.0 |

### Provenance

- **Provenance**: `LIMITED`
- **Method ID**: `dummy_energy_reader`
- **Confidence**: `0.0`
- **Fallback**: None

---

## Sampling Policy

---
**Method ID:** sampling_policy_v1
**Schema version:** v122 (sampler_stats)
**Platforms verified:** pending acceptance runs (see Platform Coverage)
**Status:** REVIEW
**Last updated:** 2026-11-08
---

### Overview

A-LEMS samples every energy source at a declared rate.
The rate a run actually uses is the lowest of three values: the rate requested by the sampling policy, the rate the machine's source can deliver, and the safe maximum for that kind of source.

f_eff = min(f_requested, f_probed, f_schema)

The policy (`config/sampling.yaml`) is versioned and machine independent: it names source kinds, never machines.
The platform refuses to measure if the policy is missing, invalid, or has no entry for a source in use.
Capability is detected automatically: on the first run on a machine, each reader is probed for about 1.5 s and the result is cached.
Every run records what was requested, what the hardware allowed, and what was achieved, so a run whose rate differs from the policy is visible in its own record.

The policy exists because two failure modes otherwise go unnoticed: rates hard coded per reader that drift apart (for example a collector polling a stream reader faster than the stream updates), and samples lost at run boundaries or on failed reads.

### Platform Coverage

| Platform | Architecture | Source | Canonical Role | Policy rate | Bound | Confidence | Status |
|----------|-------------|--------|---------------|-------------|-------|------------|--------|
| Intel i7-1165G7 platform | x86_64 | RAPL MSR counters | PACKAGE | 100 Hz | 100 Hz | 0.95 | PENDING |
| NVIDIA Grace GB10 | aarch64 | SPBM rails (hwmon, IIO) | PACKAGE, rails | 10 Hz | 10 Hz | 0.95 | PENDING |
| Apple M-series platform | arm64 | powermetrics (IOKit) | CPU_APPLE, GPU_APPLE | 10 Hz | 40 Hz | 0.95 | PENDING |
| AMD Ryzen platform | x86_64 | RAPL counters | PACKAGE | 100 Hz | 100 Hz | 0.95 | PLANNED |

Status moves to VERIFIED after acceptance runs pass invariant S2 on that platform.

### Schema

Table `sampler_stats` (schema version v122), one row per run and source.

| Column | Type | Meaning |
|--------|------|---------|
| run_id | INTEGER | run |
| source | TEXT | source kind; `none` if the run recorded no sampler stats (flagged by S2) |
| ticks, emitted | INTEGER | ticks attempted, samples written |
| empty, errors | INTEGER | ticks with no data, ticks that raised; energy is carried into the next sample |
| max_gap_ns | INTEGER | longest interval between written samples |
| late_join | INTEGER | 1 if the sampler did not stop within 5 s |
| parse_errors | INTEGER | reader parse failures in the run (powermetrics blocks without elapsed time) |
| requested_hz | REAL | policy rate |
| probed_hz | REAL | measured capability of the machine's source; NULL if unknown |
| schema_max_hz | REAL | safe maximum for the source kind |
| effective_hz | REAL | rate used |
| achieved_hz | REAL | (emitted - 1) / sampled span |
| rate_limited | INTEGER | 1 if effective_hz < requested_hz |
| policy_version, policy_sha256 | TEXT | policy in force |

### Method Provenance

* **method_id:** sampling_policy_v1, layer os, provenance CALCULATED.
* **Formula:** f_eff = min(f_requested, f_probed, f_schema); f_achieved = (n_emitted - 1) / (t_last - t_first).
* **Probe:** counter and rail readers: median interval between value changes over 1.5 s of 0.5 ms polling. Stream readers provide their own probe (powermetrics: shortest block elapsed time when asked for 1 ms blocks).
* **Energy over each interval:** counters use counter deltas; rails integrate power over the measured interval; powermetrics integrates over the elapsed time reported in each block header.
* **Confidence 0.95:** not 1.0 because the probe measures update rate from a short window and can misread a source whose update period varies with load, and because the achieved rate is computed from written samples, so a source that repeats stale values at a high rate looks faster than it is. It would reach 1.0 with vendor specified update periods for every source. Effect on results: the policy bounds sampling rate and records loss; energy totals depend on counters and integration, not on the probe.

### Query Reference

**Did every run sample at the policy rate?** Applies to all platforms. Expected: rate_limited 0, achieved within 20% of effective.

```sql
SELECT run_id, source, requested_hz, probed_hz, effective_hz,
       ROUND(achieved_hz, 1) AS achieved_hz, rate_limited
FROM sampler_stats
ORDER BY run_id DESC
LIMIT 20;
```

**Runs with sample loss.** Applies to all platforms. Expected: empty set.

```sql
SELECT run_id, source, ticks, emitted, empty, errors, late_join, parse_errors,
       ROUND(max_gap_ns / 1e6, 1) AS max_gap_ms
FROM sampler_stats
WHERE empty > 0 OR errors > 0 OR late_join = 1 OR COALESCE(parse_errors, 0) > 0
   OR source = 'none'
ORDER BY run_id DESC;
```

**Which policy produced a result set.** Applies to all platforms. Expected: one row per policy in force.

```sql
SELECT policy_version, policy_sha256, COUNT(DISTINCT run_id) AS runs
FROM sampler_stats
GROUP BY policy_version, policy_sha256;
```

### Verification

1. Policy loads: `PYTHONPATH=.:alems-sdk/src python3 -c "import core.readers.sampling_policy as p; print(p.validate()['version'])"`
2. Missing policy is refused: `ALEMS_SAMPLING_CONFIG=/nonexistent PYTHONPATH=.:alems-sdk/src python3 -c "import core.readers.sampling_policy as p; p.validate()"` raises SamplingPolicyError.
3. After one run: the queries above return one row per source, with rate_limited 0 and no loss rows.
4. Capability cache: `cat ~/.alems/sampling_probe.json` shows probed_hz per reader.
5. Invariant S2: `alems validate invariants` reports S2 PASS for the new runs.

### Known Limitations

- **Probe window:** 1.5 s cannot see update periods that change with load. Workaround: force a re-probe under load with `ALEMS_SAMPLING_REPROBE=1`.
- **Stale value sources:** a source that repeats the same value faster than it updates inflates achieved_hz. Workaround: compare probed_hz with achieved_hz; probed_hz reflects real updates.
- **Equal policy, different effective rates:** a machine that cannot reach the policy rate runs slower; rate_limited marks it. Workaround: none needed; filter on rate_limited when comparing machines.
- **Historical runs:** runs before schema version v122 have no sampler_stats rows. Workaround: the audit command reconstructs coverage from stored samples.

## Sampling Policy Version 2 { #sampling-policy-v2 }

---
**Method ID:** sampling_policy_v2
**Schema version:** v122 (sampler_stats); thermal_exposure extension migration e001
**Platforms verified:** NVIDIA Grace GB10 (GN100, aarch64)
**Status:** REVIEW
**Last updated:** 2026-11-18
---

### Overview

Version 2 of the sampling policy adds three source kinds: THERMAL_ZONE (thermal zone temperatures, 20 Hz requested), AMBIENT (an external room temperature probe, 1 Hz) and COOLING (cooling device state, 1 Hz).
Before version 2, thermal zones were read at a fixed 1 Hz outside the policy, so the achieved rate was never checked.
From version 2, every run records a THERMAL_ZONE row in `sampler_stats` with the requested, effective and achieved rate and the largest gap, and invariant S2 applies (achieved within 20 % of effective, no gap longer than two intervals).

For energy sources the rule is unchanged: f_eff = min(f_requested, f_probed, f_schema).
For thermal sources the value-change probe is evidence only and never sets the rate: f_eff = min(f_requested, f_bound), where f_bound comes from `alems_sdk.measurement_schema.SOURCE_RATE_BOUNDS`.

Three rates are kept apart: the requested polling rate (the policy), the achieved polling rate (`sampler_stats`), and the observed value-change behaviour of each sensor (the value-change probe). Repeated readings are stored; they are never dropped as stale, because a steady temperature or a coarse resolution produces repeated values even when the sensor updates.

### Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| NVIDIA Grace GB10 (GN100) | aarch64 | ACPI thermal zones, sysfs | SOC | 0.95 | VERIFIED |
| Intel i7-1165G7 | x86_64 | x86_pkg_temp sysfs | CPU_PACKAGE | 0.95 | PENDING |
| AMD Ryzen | x86_64 | k10temp sysfs | CPU_PACKAGE | 0.95 | PENDING |
| Apple M1 Pro | arm64 | IOKit (future) | CPU_PACKAGE | TBD | PLANNED |

Verified on NVIDIA Grace GB10: achieved 20.0 Hz on all three execution paths (paired, single and goal execution) and in a sandbox; largest gap 50.6 to 88.2 ms against a 100 ms limit; 100 % VALID rows.

### Schema

No column is added. Version 2 writes one more row per run into an existing table.

| Table | Change | Semantics |
|-------|--------|-----------|
| `sampler_stats` | one row with `source = 'THERMAL_ZONE'` per run | requested_hz, probed_hz (NULL: the probe never sets the thermal rate), schema_max_hz, effective_hz, achieved_hz, max_gap_ns, ticks, emitted, errors, policy_version, policy_sha256 |
| `thermal_samples_v2` | rows at the policy rate (20 Hz on NVIDIA Grace GB10) instead of 1 Hz | unchanged columns; repeated values kept |

### Method Provenance

* **method_id:** sampling_policy_v2, layer os, provenance CALCULATED, supersedes sampling_policy_v1 for new runs (v1 stays valid for older runs).
* **Formula:** f_eff = min(f_requested, f_probed, f_bound); thermal sources f_eff = min(f_requested, f_bound); f_achieved = (n_emitted - 1) / (t_last - t_first).
* **Confidence 0.95:** the achieved rate and gaps are measured directly from sample timestamps, so the rate record itself is exact. It is not 1.0 because sample times are wall clock and the polling loop can be delayed by other work on the machine (observed worst gap 88 ms at 20 Hz). Reaching 1.0 would need a real-time sampling thread. The effect on results is bounded by invariant S2: a run with a gap above two intervals is flagged.
* **Why 20 Hz:** a value-change probe on NVIDIA Grace GB10 (idle, 60 s, about 11,000 reads per second per sensor) found that all seven ACPI zones change only on multiples of about 50 ms, so 20 Hz polling is needed to see every update. The GPU temperature changes on a 500 ms grid in whole degrees. This is evidence of update behaviour, not a documented hardware rate.

### Query Reference

**Thermal sampling rate per run.** Answers: did each run reach the policy rate, and what was the largest gap?
Applies to: every platform with a thermal reader. Expected: requested 20, achieved close to 20, max gap under 100 ms on NVIDIA Grace GB10.

```sql
SELECT run_id, requested_hz, effective_hz,
       ROUND(achieved_hz, 2)       AS achieved_hz,
       ROUND(max_gap_ns / 1e6, 1)  AS max_gap_ms,
       errors, policy_version
FROM sampler_stats
WHERE source = 'THERMAL_ZONE'
ORDER BY run_id;
```

**Spacing and gaps per thermal zone.** Answers: are all zones sampled evenly, and did any gap exceed two intervals?
Applies to: every platform with registered thermal zones. Expected: mean gap 50 ms at 20 Hz, zero gaps over 100 ms.

```sql
WITH g AS (
  SELECT run_id, zone_id, quality_flag,
         timestamp_ns - LAG(timestamp_ns) OVER (PARTITION BY run_id, zone_id ORDER BY timestamp_ns) AS d
  FROM thermal_samples_v2)
SELECT run_id, zone_id, COUNT(*) AS n,
       ROUND(AVG(d) / 1e6, 1) AS mean_gap_ms,
       ROUND(MAX(d) / 1e6, 1) AS max_gap_ms,
       SUM(d > 100e6)         AS gaps_over_100ms,
       SUM(quality_flag = 'VALID') AS valid_rows
FROM g GROUP BY run_id, zone_id;
```

**Repeated readings.** Answers: how often does a zone report the same value as its previous reading (these rows are kept)?
Applies to: every platform. Expected on NVIDIA Grace GB10: 40 to 80 % repeats at idle.

```sql
WITH r AS (
  SELECT run_id, zone_id, temp_celsius AS t,
         LAG(temp_celsius) OVER (PARTITION BY run_id, zone_id ORDER BY timestamp_ns) AS p
  FROM thermal_samples_v2)
SELECT run_id, zone_id, COUNT(*) AS n, SUM(t = p) AS repeats
FROM r GROUP BY run_id, zone_id;
```

### Verification

1. `python3 -c "from core.readers import sampling_policy as p; print(p.load_policy()['version'], p.requested_hz('THERMAL_ZONE'))"` prints `2 20.0`.
2. Run one short experiment with `--save-db`.
3. Run the first query: one THERMAL_ZONE row per run, achieved within 20 % of effective, max gap at most two intervals.
4. Run the second query: every zone has the same row count and no gap over two intervals.
5. Optional evidence: `python3 scripts/tools/thermal_value_change_probe.py --seconds 60 --out <scratch file>` reports the observed value-change interval per sensor. It writes no store.

### Known Limitations

- **Polling rate is not the sensor update rate**: the probe measures how often values change, which a steady temperature or coarse resolution can hide.
  Workaround: none needed for storage; analysis uses the observed interval as evidence only.
- **Wall-clock sample times**: per-run thermal samples use wall-clock timestamps, which can jump if the system clock is stepped.
  Workaround: continuous session recording uses a monotonic time line with clock anchors.
- **GPU temperature resolution**: the GPU temperature is stored in whole degrees and updates about every 500 ms.
  Workaround: None — accept the resolution; prefer zone sensors for small temperature rises.
- **Mixed-rate stores**: runs recorded before this version used 1 Hz. Peak temperatures from 1 Hz runs can be slightly lower than from 20 Hz runs because short peaks are missed.
  Workaround: compare runs only within one policy version (`sampler_stats.policy_version`, `policy_sha256`).

## Reader Selection Logic

`ReaderFactory` selects the appropriate reader automatically:

```
PlatformDetector detects:
    OS + architecture + hardware capabilities
        ↓
ReaderFactory dispatches:
    MEASURED + Linux   → RAPLReader
    MEASURED + macOS   → IOKitPowerReader
    INFERRED           → EnergyEstimator
    LIMITED            → DummyEnergyReader
```

The selected reader's `METHOD_PROVENANCE` is stored in every run's
`measurement_methodology` rows via `reader_mode` parameter, ensuring
full audit trail of which reader was active for each experiment.
## GPU PP1 Energy Measurement

### Method ID: `gpu_rapl_pp1_v1`

**Platform:** UBUNTU2505 (Intel i7-1165G7, Tiger Lake, Iris Xe 96EU)  
**Provenance:** MEASURED  
**Confidence:** 0.95  
**Layer:** silicon

#### Background

Intel Tiger Lake exposes GPU energy via MSR_PP1_ENERGY_STATUS (MSR 0x641). This is a
package-level cumulative counter — no per-process GPU split is possible. Attribution
uses baseline subtraction, identical to the CPU dynamic energy methodology.

**Important:** The sysfs powercap path `intel-rapl:0/intel-rapl:0:1` is named `uncore`
on Tiger Lake and measures the uncore domain (L3 cache, memory controller, ring bus),
NOT the GPU. GPU energy is only accessible via MSR 0x641 directly.

#### Read Mechanism

GPU energy is read via the `msr_read` C binary (`core/msr_helper/msr_read.c`):

```
./msr_read 0 0x641   → raw 64-bit counter value
```

Energy unit from MSR 0x606 bits[12:8] = 14:

```
E_unit = 0.5^14 J = 61.0352 µJ per LSB
```

Formula:

$$E_{gpu} = (R_{end} - R_{start}) \times 61.0352\,\mu J$$

#### Cross-Validation

MSR 0x641 readings were cross-validated against the Linux perf PMU event
`power/energy-gpu/` over 5-second windows. Agreement within 7% confirms
the MSR read is correct.

#### Platform Availability

| Platform | Available | Method |
|----------|-----------|--------|
| UBUNTU2505 (Tiger Lake) | Yes | MSR 0x641 via msr_read |
| GN100 (ARM GB10) | No | No MSR interface |
| macOS | No | No /dev/cpu/N/msr |
| Other Linux x86 | Conditional | Detection via `_detect_gpu_pp1()` |

On unavailable platforms, all GPU columns store NULL. ETL skips GPU computation
when `gpu_total_energy_uj IS NULL`.

---

### Method ID: `gpu_dynamic_baseline_v1`
 
**Provenance:** CALCULATED  
**Confidence:** 0.90  
**Layer:** silicon  
**Status:** Secondary method as of 2026-06-21. Used only as fallback when a
run has zero idle-classified GPU samples for `gpu_dynamic_run_local_v1`
(below) to build a local reference from.
 
#### Formula
 
$$E_{gpu,dyn} = E_{gpu,total} - \dot{E}_{gpu,base} \cdot t$$
 
### Method ID: `gpu_dynamic_run_local_v1`
 
**Provenance:** CALCULATED  
**Confidence:** 0.90  
**Layer:** silicon  
**Status:** Primary method as of 2026-06-21.
 
#### Formula
 
$$P_{idle} = \operatorname{median}(P_i \mid \text{sample classified idle in this run})$$
 
$$E_{gpu,dyn} = \sum_i \max(P_i - P_{idle}, 0)\,\Delta t_i$$
 
#### Description
 
Idle power is estimated from the run's own idle-classified GPU samples,
not a separately-measured external calibration baseline. This removes
thermal drift, clock drift, and background load differences between
calibration time and run time. Median is used instead of mean because
idle samples occasionally contain scheduler noise or telemetry jitter;
median gives a robust estimator of steady-state idle power. Dynamic
energy is the sum of the positive residual above that local idle
reference, integrated across every sample in the run.
 
A sample is classified idle using whatever GPU activity signal is
actually available on the current platform, not a hardcoded universal
rule. On GN100 today, DCGM exposes only `util_gpu_pct`, so idle is
`util_gpu_pct == 0`. A future platform or backend reporting additional
signals gets its own classification rule without changing this formula.
 
Falls back to `gpu_dynamic_baseline_v1` only when a run has zero
idle-classified samples, GPU active the entire run, no local idle
reference available. Every run records which method was actually used
(`runs.gpu_dynamic_method`) and the literal idle power value applied
(`runs.gpu_idle_power_w_used`), so the split between the two methods
across any set of runs is a direct query, not an estimate.
 
#### Validated against
 
Confirmed on GN100 in two real same-experiment runs: external
calibration, measured once and separately, produced baseline-exceeds-
total in opposite directions depending on run duration and idle-time
fraction within the run, the wrong run looked correct and the right run
looked like zero. Run-local estimation resolved both by referencing
each run's own conditions instead of one fixed external number.
 

Where:
- $E_{gpu,total}$ = SUM of gpu_energy_uj across all energy_samples for the run
- $\dot{E}_{gpu,base}$ = `idle_baselines.gpu_power_watts` (mean - 2*std, 2nd percentile)
- $t$ = run duration in seconds

#### Baseline Measurement

GPU baseline is measured during `measure_idle_baseline()` when `measure_gpu: true`
in `app_settings.yaml`. Each idle sample reads MSR 0x641 at start and end of the
sample window, computing GPU power in Watts. Mean and std are stored in
`idle_baselines.gpu_power_watts` and `idle_baselines.gpu_std`.

#### Columns Populated

| Column | Table | Description |
|--------|-------|-------------|
| `gpu_total_energy_uj` | `runs` | SUM(gpu_energy_uj) over all energy_samples |
| `gpu_baseline_energy_uj` | `runs` | idle GPU rate * run_duration_ns |
| `gpu_dynamic_energy_uj` | `runs` | gpu_total - gpu_baseline |
| `gpu_pct_of_pkg` | `runs` | gpu_dynamic / dynamic_energy_uj * 100 |
| `gpu_energy_uj` | `goal_attempt` | gpu_dynamic_energy_uj for this attempt |
| `gpu_total_energy_uj` | `goal_execution` | SUM across attempts |
| `gpu_pct_of_pkg` | `goal_execution` | GPU share of goal energy |

#### Paper Finding

Integrated GPU energy (Intel Iris Xe, MSR PP1) accounts for a higher percentage
of dynamic package energy during agentic workflows than linear workflows. This
increase is driven by orchestration overhead — not ML compute, since no CUDA or
local GPU inference is used in any experiment.

---

## Device Telemetry

Device telemetry captures instantaneous device state at 10 Hz alongside energy
sampling. It is distinct from energy measurement: power is instantaneous (mW),
energy is cumulative (µJ). They have different conservation properties and
different consumers.

### What Device Telemetry Captures

| Field | Unit | Description |
|-------|------|-------------|
| power_mw | mW | Instantaneous device power draw |
| energy_uj | µJ | Cumulative energy delta (nullable) |
| util_pct | % | Device utilization 0 to 100 |
| temp_c | °C | Device temperature |
| clock_mhz | MHz | Device clock frequency |
| dc_input_mw | mW | Wall input power (SPBM dc_input, SOC only) |
| mem_util_pct | % | Memory utilization (GPU only) |

### Device Types

```
GPU      discrete or integrated GPU (NVML, DCGM, IOKit)
SOC      full SoC telemetry (SPBM dc_input channel, GN100)
CPU      CPU frequency and C-state telemetry (future)
NETWORK  network device telemetry (future RDMA)
STORAGE  storage device telemetry (future NVMe)
```

### energy_uj Nullability

`energy_uj` is present when the backend provides a cumulative counter:

- NVML: `nvmlDeviceGetTotalEnergyConsumption()` returns cumulative mJ
- DCGM: field 156 returns cumulative mJ
- IOKit: integrated power sensor

`energy_uj` is NULL for `SMI_INTEG` (nvidia-smi power integration). In that
case power_mw is recorded and ETL integrates W x dt at run completion.

### dc_input_mw (GN100 SOC)

SPBM exposes a `dc_input` power channel giving wall input power to the SoC.
This is stored in `device_telemetry` with `device_type='SOC'`. It is the
closest equivalent to a wall power meter on the GN100 platform and is used
for total system energy accounting in the NVLink-C2C paper.

### Sampling Rate

Device telemetry is sampled at 10 Hz, aligned with `energy_samples_v2` timestamps.
This allows per-sample correlation between energy domain values and device state.

### method_id

`device_telemetry_v1` — confidence 1.0 (MEASURED: direct hardware interface reads)

---

## ARM PMU Reader (arm_pmu_v1)

**Platform:** GN100 (NVIDIA Grace, Neoverse V2, aarch64)
**method_id:** `arm_pmu_v1`
**Confidence:** 0.95
**Provenance:** MEASURED
**Layer:** silicon

### What It Measures

ARM Neoverse V2 performance counters via Linux `perf stat`. Attaches to the
target process PID during the measurement window. Uses generic event names
(`instructions`, `cycles`) for core IPC metrics and `armv8_pmuv3/` prefixed
events for cache hierarchy where no generic alias exists on ARM Linux.

### Column Mapping

| runs column | ARM PMU event | Notes |
|---|---|---|
| instructions | `instructions` | retired instruction count |
| cycles | `cycles` | CPU cycle count |
| ipc | derived | instructions / cycles |
| cache_misses | `armv8_pmuv3/l1d_cache_refill/` | L1D refill count |
| cache_references | `armv8_pmuv3/l1d_cache/` | L1D access count |
| l1d_cache_misses_total | `armv8_pmuv3/l1d_cache_refill/` | same as cache_misses |
| l2_cache_misses_total | `armv8_pmuv3/l2d_cache_refill/` | L2D refill count |
| l3_cache_hits_total | `armv8_pmuv3/l3d_cache/` | L3D hit count |
| l3_cache_misses_total | `armv8_pmuv3/l3d_cache_refill/` | L3D refill count |

### Confidence Note

0.95 not 1.0: ARM PMU event multiplexing may cause minor undercounting when
more events are requested than hardware PMU registers available on the core.
Neoverse V2 has 6 programmable counters. Seven events are requested — one
counter slot is time-multiplexed, introducing small statistical error.

### x86 Equivalent

`PerfReader` on UBUNTU2505 and Alex AMD machine. Factory dispatches
`ARMPMUReader` only when `caps.arch == 'aarch64'` and `caps.has_arm_pmu`.

---

## ARM cpufreq Reader (arm_cpufreq_v1)

**Platform:** GN100 (NVIDIA Grace, Neoverse V2, aarch64)
**method_id:** `arm_cpufreq_v1`
**Confidence:** 0.90
**Provenance:** MEASURED
**Layer:** os

### What It Measures

CPU operating frequency via Linux cpufreq sysfs at 10 Hz during the
measurement window. Reads `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
(kHz, converted to MHz) for all online CPUs and returns the time-averaged
frequency across all cores and all sample points.

### Column Mapping

| runs column | Source | Notes |
|---|---|---|
| frequency_mhz | cpufreq sysfs avg | mean across all CPUs and window |
| cpu_avg_mhz | cpufreq sysfs avg | same value |
| c2_time | NULL | ARM WFI/WFE not equivalent to x86 c-states |
| c3_time | NULL | ARM WFI/WFE not equivalent to x86 c-states |
| c6_time | NULL | ARM WFI/WFE not equivalent to x86 c-states |
| c7_time | NULL | ARM WFI/WFE not equivalent to x86 c-states |
| ring_bus_freq_mhz | NULL | no turbostat on ARM |
| voltage_vcore_avg | NULL | no MSR on ARM |

### NULL is Correct for C-states

ARM uses WFI (Wait For Interrupt) and WFE (Wait For Event) idle states.
These are not equivalent to x86 C2/C3/C6/C7 residency states. Storing NULL
per MIC-1 is scientifically correct — do not estimate or map to zero.

### Confidence Note

0.90 not 1.0: sysfs polling at 10 Hz has ~100ms granularity. TurbostatReader
on x86 uses MSR timestamps at ~10ms resolution. Frequency transitions faster
than 100ms are averaged out. On Grace under LLM inference load, frequency is
typically stable at or near maximum, so the practical impact is small.

### x86 Equivalent

`TurbostatReader` on UBUNTU2505 and Alex AMD machine. Factory dispatches
`ARMCPUFreqReader` only when `caps.os == 'Linux'` and `caps.arch == 'aarch64'`.


---

## ARM Thermal Reader (arm_thermal_sysfs_v1)

### Overview

**method_id:** `arm_thermal_sysfs_v1`
**confidence:** 0.90
**platform:** GN100 (aarch64, NVIDIA Grace)
**reader class:** `ARMThermalReader`

Reads thermal zone temperatures via `/sys/class/thermal/thermal_zone*/temp` on ARM
Linux. Used on GN100 where `SensorReader` returns empty results because it requires
x86-specific hwmon paths from `hw_config["thermal"]["paths"]` that are absent on Grace.

### GN100 Thermal Zones (Verified 2026-06-18)

| Zone | Type | Idle Temp |
|------|------|-----------|
| thermal_zone0 | acpitz | 44.8°C |
| thermal_zone1 | acpitz | 43.8°C |
| thermal_zone2 | acpitz | 43.7°C |
| thermal_zone3 | acpitz | 43.8°C |
| thermal_zone4 | acpitz | 43.7°C |
| thermal_zone5 | acpitz | 43.8°C |
| thermal_zone6 | acpitz | 44.8°C |

All 7 zones report `type=acpitz` (ACPI thermal zone). No MSR or hwmon thermal
interface is available on the Grace SoC.

### Column Mapping

| runs column | Source | Notes |
|-------------|--------|-------|
| package_temp_celsius | avg(all 7 acpitz zones) | averaged for stability |
| start_temp_c | first sample in run | from thermal_samples ETL |
| max_temp_c | max across run | from thermal_samples ETL |
| min_temp_c | min across run | from thermal_samples ETL |
| thermal_delta_c | max_temp_c - start_temp_c | derived |

### Why Average 7 Zones

All zones report ACPI package-level temperature derived from the same SoC thermal
diode network. Averaging gives a stable value robust to per-zone ACPI polling jitter.

### Confidence Note

0.90 not 1.0: ACPI thermal zones have a polling interval of 100-200ms, introducing
lag relative to true silicon temperature. Under sustained LLM inference this averages
out. Peak temperature excursions shorter than 200ms may be missed.

### NULL is Correct for GPU Temperature

GPU temperature on GN100 is available via `nvidia-smi` and DCGM. `ARMThermalReader`
does not populate `gpu_celsius` — it returns 0.0 per the ABC contract. GPU temperature
attribution is handled separately by the DCGM backend.

### x86 Equivalent

`SensorReader` on UBUNTU2505 and Alex AMD machine. Factory dispatches
`ARMThermalReader` only when `caps.os == 'Linux'` and `caps.arch == 'aarch64'`
and `caps.has_thermal`.

## Apple Silicon PMU Counters (kperf_pmu_v1)

---
**Method ID:** kperf_pmu_v1
**Schema version:** No new columns (all columns pre-exist in runs table)
**Platforms verified:** Apple M1 Pro (arm64)
**Status:** DRAFT
**Last updated:** 2026-08-13
---

### Overview

Reads CPU performance counters (instructions retired, CPU cycles, L1D
cache misses) on Apple Silicon via Apple's private kperf.framework and
kperfdata.framework. A compiled C helper binary performs the actual PMU
read and prints JSON to stdout. The Python reader (`KPerfPMUReader`) calls
the helper at measurement start and stop, then computes deltas.

This method fills the same `runs` table columns as `PerfReader` on Linux:
`instructions`, `cycles`, `ipc`, `cache_misses`, `cache_references`,
`cache_miss_rate`, `l1d_cache_misses_total`, `energy_per_instruction`,
`energy_per_cycle`. L2 and L3 cache columns remain NULL on M1 Pro — this
is a hardware PMU event limitation of the a14 core architecture, not a
software gap.

### Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| Apple M1 Pro | arm64 | kperf/kperfdata PMU | CPU_COUNTERS | 0.85 | DRAFT |
| Apple M2 (expected) | arm64 | kperf/kperfdata PMU | CPU_COUNTERS | TBD | PLANNED |
| NVIDIA Grace GB10 | aarch64 | N/A | N/A | N/A | NOT_SUPPORTED |
| Intel i7-1165G7 | x86_64 | N/A | N/A | N/A | NOT_SUPPORTED |
| AMD Ryzen | x86_64 | N/A | N/A | N/A | NOT_SUPPORTED |

### Schema

No new columns. All target columns already exist in the `runs` table:

| Column | Type | Apple Silicon Source | Linux Source |
|--------|------|---------------------|--------------|
| instructions | REAL | FIXED_INSTRUCTIONS delta | perf instructions |
| cycles | REAL | FIXED_CYCLES delta | perf cycles |
| ipc | REAL | instructions / cycles | perf derived |
| cache_misses | REAL | L1D_MISS_LD + L1D_MISS_ST | perf cache-misses |
| cache_references | REAL | L1D_TLB_ACCESS (proxy) | perf cache-references |
| cache_miss_rate | REAL | cache_misses / cache_references | perf derived |
| l1d_cache_misses_total | INTEGER | L1D_CACHE_MISS_LD_NONSPEC | perf L1-dcache-load-misses |
| l2_cache_misses_total | INTEGER | NULL (no M1 event) | perf l2_rqsts.miss |
| l3_cache_hits_total | INTEGER | NULL (no M1 event) | perf LLC-loads |
| l3_cache_misses_total | INTEGER | NULL (no M1 event) | perf LLC-load-misses |
| energy_per_instruction | REAL | pkg_energy_uj / instructions | same |
| energy_per_cycle | REAL | pkg_energy_uj / cycles | same |

### Method Provenance

**method_id:** kperf_pmu_v1
**confidence:** 0.85
**layer:** silicon
**provenance:** MEASURED

**Formula:**

    delta_instr = C_stop^instr - C_start^instr

where C is the system-wide PMU counter value read by the C helper at
measurement start and stop boundaries.

**Confidence justification (PDS-6):**

(a) Why not 1.0: Three sources of uncertainty. First, kperf and kperfdata
are private Apple frameworks — stable since macOS 12 through macOS 15+
but with no public API guarantee. Second, `kpc_set_config()` requires
root, adding a sudoers installation step and ~50ms subprocess overhead
per call. Third, counters are system-wide across all CPUs and processes,
not scoped to the inference workload.

(b) To reach 1.0: Apple would need to publish kperf as a public stable
API, remove the root requirement, and provide per-process PMU sampling.

(c) Quantitative impact: During llama_cpp inference on a quiescent M1 Pro,
the inference process accounts for ~92–98% of CPU utilization (verified
via top). System-wide overcounting of instructions/cycles is estimated at
2–8%. For runs exceeding 5 seconds this is within acceptable research
bounds.

### Query Reference

**Verify PMU counters populated on Apple Silicon:**

Answers: Are instructions and cycles non-zero after a Mac run?
Applies to: Apple M1 Pro (arm64). Replace `<your-hostname>` with output
of `python3 -c "import socket; print(socket.gethostname())"`.
Expected: instructions 10^8 to 10^11 per run, IPC 1.0 to 4.0.

```sql
SELECT run_id, created_at,
       instructions, cycles, ipc,
       cache_misses, cache_miss_rate,
       l1d_cache_misses_total,
       energy_per_instruction, energy_per_cycle
FROM runs
WHERE machine_id = '<your-hostname>'
  AND instructions > 0
ORDER BY run_id DESC
LIMIT 10;
```

**Cross-platform IPC comparison:**

Answers: How does IPC differ across measurement platforms?
Applies to: All platforms with PMU counters populated.

```sql
SELECT machine_id,
       AVG(ipc) AS avg_ipc,
       MIN(ipc) AS min_ipc,
       MAX(ipc) AS max_ipc,
       COUNT(*) AS num_runs
FROM runs
WHERE ipc > 0
GROUP BY machine_id;
```

### Verification

Run these commands on the Apple M1 Pro platform after implementation:

```bash
# 1. Compile and install the C helper
cc -O2 -o scripts/helpers/kperf_reader scripts/helpers/kperf_reader.c
sudo cp scripts/helpers/kperf_reader /usr/local/bin/alems_kperf_reader
sudo chown root:wheel /usr/local/bin/alems_kperf_reader
sudo chmod 755 /usr/local/bin/alems_kperf_reader

# 2. Install sudoers rule (or run scripts/fix_permissions.sh)
echo "%admin ALL=(root) NOPASSWD: /usr/local/bin/alems_kperf_reader" \
    | sudo tee /etc/sudoers.d/alems_kperf > /dev/null
sudo chmod 0440 /etc/sudoers.d/alems_kperf

# 3. Verify helper produces JSON without password prompt
sudo -n /usr/local/bin/alems_kperf_reader
# Expected: {"instructions":NNNN,"cycles":NNNN,"l1d_miss_ld":NN,...}

# 4. Run standalone verification test
python3 scripts/tests/test_kperf_standalone.py
# Expected: all PASS, 0 FAIL

# 5. Verify factory returns KPerfPMUReader on Darwin arm64
python3 -c "
from core.readers.factory import ReaderFactory
r = ReaderFactory.get_cpu_reader()
print(type(r).__name__)
"
# Expected: KPerfPMUReader

# 6. Run experiment and verify DB columns
DB=$(python3 -c "from scripts.tools.path_loader import get_alems_db_path; print(get_alems_db_path())")
sqlite3 "$DB" "
SELECT run_id, instructions, cycles, ipc,
       cache_misses, l1d_cache_misses_total,
       energy_per_instruction
FROM runs ORDER BY run_id DESC LIMIT 1;
"
# Expected: instructions > 0, cycles > 0, 0.5 < ipc < 5.0
# l2/l3 cache columns NULL — correct, hardware limitation
```

### Known Limitations

- **L2 and L3 cache events unavailable on M1:** The a14.plist (M1 core
  architecture) does not include L2 cache miss, L3 cache miss, or L3
  cache hit events. `l2_cache_misses_total`, `l3_cache_hits_total`, and
  `l3_cache_misses_total` remain NULL on M1 Pro. Hardware limitation,
  not a software gap. Workaround: None. Accept NULL.

- **System-wide counters, not per-process:** Counters capture all CPU
  activity across all cores and processes. Overcounting estimated at 2–8%
  during dedicated inference. Workaround: Ensure Mac is quiescent.

- **Sudo required:** Without the sudoers rule, reader falls back to
  DummyCPUReader (all counters zero/NULL). Workaround: Run
  `scripts/fix_permissions.sh`.

- **VM environments:** kperf unavailable in virtualized macOS. Reader
  returns False from `is_available()` and falls back gracefully.
  Workaround: None — PMU counters require bare metal macOS.

- **Private API stability:** Stable macOS 12 through 15+ but no formal
  guarantee. `is_available()` catches load failures. Workaround: Monitor
  macOS release notes.

- **Subprocess overhead:** ~50ms per call, ~100ms per measurement. Under
  2% overhead for runs >5 seconds. Significant for sub-second runs.

- **cache_references proxy:** `L1D_TLB_ACCESS` used as proxy for total
  cache accesses. Cross-platform `cache_miss_rate` comparisons should
  account for this semantic difference vs Linux perf LLC-accesses.

---

## GPU Dual Channel Energy Measurement

### Overview

On GN100 (NVIDIA Grace GB10) GPU energy is available from two independent instruments: DCGM (field 156, GPU compute) and the SPBM hwmon GPU rail (domain_id 7, a broad rail covering GPU compute, GPU memory, NVLink-C2C and other on package GPU consumers). Three methods use the SPBM rail.

### Method ID: `gpu_spbm_total_v1`

Total GPU energy from the SPBM broad rail, the sum of per interval deltas written by SPBMEnergyReader into energy_sample_domains for domain_id 7 over the run.

$$E_{gpu,spbm} = \sum_i \Delta E_{gpu,spbm,i}$$

Output column gpu_spbm_total_uj (µJ). GN100 only: on hardware without SPBM the domain has no rows and the value is NULL (MIC-3), not an error. Verified 2026-06-21 on run 90: SPBM total 231.0 J versus DCGM 174.2 J for the same run, SPBM above DCGM as expected for a broader rail.

### Method ID: `gpu_spbm_dynamic_v1`

SPBM rail energy minus its idle baseline (domain_id 7 idle power from idle_baseline_domains times run duration), the same rate times duration pattern as the DCGM baselined value.

$$E_{gpu,spbm,dyn} = \max(0,\ E_{gpu,spbm,total} - E_{gpu,spbm,baseline})$$

Clamped to zero: a negative value here indicates a baseline problem, not a signal.

### Method ID: `gpu_residual_dynamic_v1`

Residual GPU energy outside compute, mainly GPU memory traffic and NVLink-C2C (hypothesized; not decomposable with current instrumentation).

$$E_{gpu,residual} = E_{gpu,spbm,dyn} - E_{gpu,dyn}$$

Not clamped: a negative residual is a valid diagnostic of baseline drift or window misalignment between the two instruments. Baseline uncertainty enters twice, so any paper using this value must report confidence intervals, not only point estimates.

### Known Limitations

Whether the SPBM GPU rail is inside the SPBM package counter is not yet verified (B33 design, experiment 8.1). DCGM and SPBM measure the same physical GPU with different boundaries and must never be added together.

---

## SPBM Full Power Telemetry

### Overview

SPBM exposes six power channels on GN100 that have no hardware cumulative counter: SOC_PKG, CPU_GPU, VCORE, DC_INPUT, PREREG, DLA. Their energy is computed by integrating sampled power.

### Method ID: `SPBM_INSTANTANEOUS_POWER_INTEGRATION_V1`

$$E_{rail} = \sum_i P_{rail,i} \cdot \Delta t_i$$

Rectangular integration at the sampling rate of the run (10 Hz policy). One parameterised method covers all six rails. Raw power readings are MEASURED; integrated energy is CALCULATED. No numeric confidence is asserted until a characterisation against a known load is done (open item). Trapezoidal integration was considered and deferred.

### Storage and units

Each sample stores rail energy for that sample interval in microjoules.
The interval is the measured time between consecutive ticks, not the planned sampling period, so a late tick integrates its full duration.
Conversion: $E_{\mu J} = P_{mW} \cdot \Delta t_{ns} / 10^6$; for example 50,000 mW over a measured 100 ms gives 5,000,000 µJ.
Rows are written to `energy_sample_domains` with domain ids 20 (DLA) and 25 to 29.
The catalog `energy_domains` labels these domains `unit = 'uj'` and `is_cumulative = 0` (seed files s021 and s022): each row is energy for one interval, not a running counter, so per run energy is the sum of rows.

Verification (any store with GN100 runs; the ratio should lie between 0.99 and 1.01):

```sql
-- Stored SOC_PKG energy vs the rail power integral for the latest run.
WITH r AS (SELECT MAX(run_id) AS run_id FROM energy_sample_domains WHERE domain_id = 25),
 st AS (SELECT SUM(energy_uj) AS stored_uj FROM energy_sample_domains
        WHERE domain_id = 25 AND run_id = (SELECT run_id FROM r)),
 rl AS (SELECT SUM(s.power_mw * s.interval_ns / 1e6) AS rail_uj
        FROM power_rail_samples s JOIN power_rails p ON p.rail_id = s.rail_id
        WHERE p.rail_name = 'soc_pkg' AND s.run_id = (SELECT run_id FROM r))
SELECT stored_uj, rail_uj, ROUND(stored_uj / rail_uj, 4) AS ratio FROM st, rl;
```

### Method ID: `spbm_conversion_loss_v1`

$$E_{loss} = E_{dc\_input} - E_{pkg}$$

Difference between the DC_INPUT rail and the package counter. Hypothesized to be conversion and voltage regulation loss outside the package, but the physical measurement point of DC_INPUT is not verified against NVIDIA documentation; until it is, this value must not be called board, wall or system input power in any paper. It is documented only as the difference between two named rails.

### Method ID: `spbm_conversion_efficiency_v1`

$$\eta = E_{pkg} / E_{dc\_input}$$

Dimensionless ratio, normally within [0, 1]; the same DC_INPUT caution applies. Provided because ratios compare more easily across workloads of different length and power than absolute differences.

### Known Limitations

DC_INPUT and SYS_TOTAL have been observed to disagree; DC_INPUT is treated as a diagnostic rail, not a contract boundary (B33 design 4.6).

- **Stores written before the energy storage fix hold power, not energy**: in stores whose rail rows were written by A-LEMS before commit 9764052, domains 25 to 29 hold instantaneous power in mW per sample, not energy.
  The catalog relabel (s021, s022) changes labels only and never rewrites stored samples.
  Workaround: check `environment_config.git_commit` of the experiment; for older commits, integrate `power_rail_samples` instead of summing these domains.
- **VCORE and PREREG are not additive**: they overlap other rails and must never appear in a sum with CPU or package rails.
  Workaround: report them as separate channels.