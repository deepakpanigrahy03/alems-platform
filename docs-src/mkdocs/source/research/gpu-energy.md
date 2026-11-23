# GPU Energy Measurement Methodology


---

## Overview

A-LEMS measures GPU energy alongside CPU energy to produce a complete system
energy picture for agentic AI workloads. GPU energy is collected at 10 Hz via
a backend-abstracted `GPUCollector` that automatically selects the appropriate
measurement source for each platform.

GPU and CPU energy are **disjoint physical quantities** — no conservation
relationship exists between them. Each domain has its own attribution chain
and its own conservation invariants (D5–D8 for GPU, D1–D4 for CPU).

---

## Physical Model

On Intel x86 platforms, the package energy domain decomposes as:

```
PKG = core + uncore + remainder
```

Where:
- `core` — CPU cores (measured via intel-rapl:0:0)
- `uncore` — L3 cache and ring bus (measured via intel-rapl:0:1, named "uncore" in sysfs)
- `remainder` — everything else (~36% of PKG on UBUNTU2505 i7-1165G7)

**Critical:** `intel-rapl:0:1` sysfs domain is named "uncore" but refers to the
L3/ring bus, NOT the GPU. GPU energy is NOT available via sysfs powercap on
Intel Tiger Lake. It must be read via MSR 0x641 (MSR_PP1_ENERGY_STATUS).

GPU is a sub-component of `remainder`:

```
PKG >= core + GPU    (D4-extended invariant — always holds)
GPU ⊂ remainder      (GPU never overlaps core or uncore)
```

---

## Per-Platform Backend Matrix

| Platform | Hardware | Backend | Energy Source | Confidence |
|----------|----------|---------|---------------|-----------|
| UBUNTU2505 | Intel i7-1165G7 + Iris Xe | `msr_pp1` | MSR 0x641 | 1.0 |
| GN100 | NVIDIA GB10 Superchip (ARM) | `dcgm` | DCGM field 156 | 1.0 |
| Alex machine | AMD Ryzen + RTX 2070 Super | `nvml` | NVML totalEnergyConsumption | 1.0 |
| Stephen M1 | Apple M1 Pro | `iokit` | powermetrics gpu_power | 0.90 |
| AMD GPU (future) | AMD discrete | `rocm_smi` | rsmi_dev_energy_count_get | 0.85 |
| Unknown | Any | `none` | Not available | N/A |

Backend detection runs once at `GPUCollector.__init__()`. Detection order:
DCGM → NVML → MSR PP1 → ROCm → IOKit → None. First available wins.
The `source` column in `gpu_samples` records which backend produced each sample.

---

## MSR PP1 Backend (UBUNTU2505)

**MSR address:** 0x641 (MSR_PP1_ENERGY_STATUS)

**Energy unit:** 61.0352 µJ per LSB

Derived from MSR 0x606 (MSR_RAPL_POWER_UNIT) bits[12:8] = 14:

```
energy_unit = 2^(-14) J = 61.0352 µJ/LSB
```

**Verification:**
```bash
~/mydrive/alems-platform/core/msr_helper/msr_read 0 0x641
# Returns raw counter, e.g. 123689383
# Idle GPU: ~50 mW (0.10 J over 2 seconds)
```

Cross-validated against `perf stat -e power/energy-gpu/` with 7% agreement.
No sudo required on UBUNTU2505 (msr module loaded, group permissions set).

**Limitation:** MSR PP1 exposes energy only. No utilization, clock, memory,
or temperature signals via this backend. Those `gpu_samples` columns are NULL
for `source = 'msr_pp1'`.

---

## DCGM Backend (GN100)

**DCGM field:** 156 (DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION)

Cumulative mJ counter. Validated on GN100 GB10 Superchip with
`spark_hwmon` driver loaded (45/45 DSM offsets confirmed).

RAPL does not exist on the GB10 ARM platform. DCGM is the only validated
GPU energy path on DGX/HGX systems. This is a vendor product decision,
not a hardware limitation (documented in LOCO Workshop paper arXiv:2605.27599).

---

## NVML Backend (NVIDIA Discrete GPUs)

**NVML function:** `nvmlDeviceGetTotalEnergyConsumption()`

Returns cumulative mJ counter. Converted to µJ for consistency with all
other A-LEMS energy columns.

Fallback for older drivers: `nvmlDeviceGetPowerUsage()` returns instantaneous
mW. GPUCollector integrates power × Δt. Confidence drops to 0.85 for
power-integration fallback (method_id: `nvml_power_integration_v1`).

Additional signals available via NVML: utilization, SM clock, memory clock,
memory used, temperature. All populated in `gpu_samples` signal columns.

---

## Sampling Architecture

`GPUCollector` runs in a background daemon thread at **10 Hz** alongside the
existing `HighFrequencySampler` (RAPL at 100 Hz). Rate rationale:

- NVML caps at ~50 Hz; 10 Hz balances detail vs collector overhead
- Matches `cpu_samples` and `interrupt_samples` rate
- Provides sufficient temporal resolution for phase alignment with
  `orchestration_events` phase boundaries (D8 proxy method)

**Queue policy:** oldest-drop (same as `HighFrequencySampler`). Queue size
2000 samples = ~3.3 minutes at 10 Hz before drop occurs.

**Counter wraparound:** MSR 0x641 is a 32-bit counter. Wraparound detected
by negative delta — sample skipped, warning logged, previous state updated.
At 61.0352 µJ/LSB and 50 mW idle, wraparound occurs after ~72 hours of
continuous measurement. Not a practical concern for A-LEMS run durations.

---

## DCGM Readiness and Shared Window Coverage { #dcgm-shared-window-coverage-v1 }

### Overview
GPU energy from DCGM field 156 is only as good as its coverage of the run.
Three rules protect it.
The sampler waits for a first valid field 156 reading (blank values occur until the field watch delivers its first sample) before the energy window opens, and that reading becomes the baseline of the first delta.
The sample buffer is unbounded, so long runs keep every sample.
When the sampler stops it takes one final reading, so the interval after the last tick is kept.
Each run gets a coverage record in `energy_validation_dcgm_coverage`.

### Platform Coverage
| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 (GN100) | aarch64 | DCGM field 156 | GPU energy coverage | 0.95 | VERIFIED |
| NVIDIA discrete GPUs (NVML) | x86_64 | NVML energy counter | GPU energy coverage | 0.95 | PENDING |
| Intel, AMD, Apple | any | other GPU backends | same record when a backend is available | 0.95 | PENDING |

### Schema
| Column | Type | Meaning |
|---|---|---|
| run_id | INTEGER | run, primary key |
| source | TEXT | GPU backend (dcgm) |
| ready_status | TEXT | ready, timeout or unavailable |
| ready_wait_ms | REAL | time spent waiting for the first valid reading |
| sampler_start_utc_ns | INTEGER | sampler start, wall clock epoch ns |
| first_valid_utc_ns, last_valid_utc_ns | INTEGER | span of valid samples, wall clock epoch ns |
| first_valid_mono_ns, last_valid_mono_ns | INTEGER | same span on the monotonic clock (alignment only) |
| row_count | INTEGER | stored gpu_samples rows |
| interior_gaps_gt_300ms | INTEGER | consecutive sample ends more than 300 ms apart |
| max_gap_ms | REAL | largest such distance |
| shared_window_coverage | REAL | share of the run window covered, NULL when not computable |
| coverage_null_reason | TEXT | readiness_timeout, run_window_unknown or no_valid_samples |
| cap_detected | INTEGER | 1 if any sample was dropped (only possible through a regression) |

### Method Provenance
Method `dcgm_shared_window_coverage_v1`, CALCULATED, layer silicon, confidence 0.95.

$$C = \frac{\min(t_{last}, t_{end}) - \max(t_{first}, t_{start})}{t_{end} - t_{start}}$$

Confidence 0.95, not 1.0: the record proves which interval DCGM samples span, not how often the underlying counter updates; row cadence (10 Hz) is not the counter's value change rate, which is measured separately.
The run window end is stamped after measurement teardown, about 100 to 200 ms after the GPU sampler stops, so coverage is slightly below 1.0 even when no data is lost (under 0.1% for runs of 250 s or more).
To reach 1.0 the window end would be stamped when the samplers stop.

### Query Reference
**Runs whose GPU energy coverage is below 0.99 or not computable.** Applies to every platform with a GPU energy backend; typical coverage on GB10 is 0.991 to 0.999.
```sql
SELECT run_id, ready_status, row_count, interior_gaps_gt_300ms,
       ROUND(shared_window_coverage, 4) AS coverage, coverage_null_reason
FROM energy_validation_dcgm_coverage
WHERE shared_window_coverage IS NULL OR shared_window_coverage < 0.99
ORDER BY run_id;
```
**Where a gap over 300 ms occurred in one run, and whether its energy was kept** (replace 6 with a run id). A gap whose `interval_ms` equals `gap_ms` with positive energy is a late tick whose energy is kept.
```sql
WITH g AS (SELECT sample_end_ns, interval_ns, energy_uj,
                  LAG(sample_end_ns) OVER (ORDER BY sample_end_ns) AS prev_end
           FROM gpu_samples WHERE run_id = 6)
SELECT ROUND((sample_end_ns - prev_end) / 1e6, 1) AS gap_ms,
       ROUND(interval_ns / 1e6, 1) AS interval_ms, energy_uj
FROM g WHERE sample_end_ns - prev_end > 300000000;
```

### Verification
1. Run one goal of 250 s or more.
2. Check that `row_count` is above 2,000 (a capped buffer stops at 2,000 at 10 Hz), `shared_window_coverage` is 0.99 or more and `cap_detected` is 0.
3. Run `python scripts/test_exp_integrity.py --latest` and confirm the dcgm_coverage check passes.

Example output from NVIDIA Grace GB10 platform: a 419.9 s run stored 4,198 rows with coverage 0.9994, no cap, and one late tick of 522.8 ms whose energy was kept.

### Known Limitations
- **Counter update rate**: the record does not measure how often field 156 changes value. Workaround: measure update behaviour separately (value change intervals at idle and load).
- **Run window end**: stamped after teardown, so coverage under 1.0 does not by itself mean lost data. Workaround: use the gap query and `cap_detected` to tell lost data from window length.
- **Historical stores**: runs recorded before this record existed have no row; their coverage must be computed from `gpu_samples` and `runs` directly. Workaround: shared window comparisons only.

## gpu_samples Table

```sql
CREATE TABLE gpu_samples (
    sample_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(run_id),
    gpu_index        INTEGER NOT NULL DEFAULT 0,  -- 0-based, multi-GPU support
    sample_start_ns  BIGINT NOT NULL,
    sample_end_ns    BIGINT NOT NULL,
    interval_ns      BIGINT NOT NULL,
    energy_start_uj  BIGINT,   -- cumulative counter at sample start
    energy_end_uj    BIGINT,   -- cumulative counter at sample end
    energy_uj        BIGINT,   -- delta (denorm for query speed)
    power_mw         INTEGER,  -- instantaneous power (NVML fallback / IOKit)
    util_gpu_pct     REAL,     -- NULL for msr_pp1 backend
    util_mem_pct     REAL,     -- NULL for msr_pp1 backend
    sm_clock_mhz     INTEGER,  -- NULL for msr_pp1 backend
    mem_clock_mhz    INTEGER,  -- NULL for msr_pp1 backend
    mem_used_mb      INTEGER,  -- NULL for msr_pp1 backend
    temperature_c    INTEGER,  -- NULL for msr_pp1 backend
    source           TEXT NOT NULL  -- backend identifier
);
```

---

## gpu_config Table

One row per physical GPU per machine. Populated by `scripts/detect_gpu.py`.

```sql
CREATE TABLE gpu_config (
    gpu_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gpu_index         INTEGER NOT NULL DEFAULT 0,
    vendor            TEXT NOT NULL,   -- 'intel'|'nvidia'|'amd'|'apple'
    model             TEXT NOT NULL,
    driver_version    TEXT,
    cuda_version      TEXT,
    rocm_version      TEXT,
    vbios_version     TEXT,
    pci_id            TEXT,
    memory_total_mb   INTEGER,
    energy_supported  INTEGER NOT NULL DEFAULT 0,
    backend           TEXT,
    gpu_hash          TEXT NOT NULL,   -- SHA256(model||pci_id||driver_version)
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(gpu_index, gpu_hash)
);
```

`gpu_hash` tracks hardware identity across driver upgrades. A new row is
inserted when `gpu_hash` changes (driver or VBIOS update detected).

---

## Attribution Method

`runs.gpu_attribution_method` records how `gpu_dynamic_energy_uj` was computed:

| Value | Meaning | When used |
|-------|---------|-----------|
| `exclusive` | Workload had exclusive GPU use | All current A-LEMS experiments (single-process) |
| `utilization` | GPU shared between processes | Future multi-tenant work |
| `none` | GPU not used or counters unavailable | NoneBackend or no GPU |

**B-decision:** `gpu_dynamic_energy_uj` is the canonical attributed GPU energy
metric. The canonical column is `gpu_dynamic_energy_uj` — equivalent in definition to `gpu_attributed_energy_uj`.
No separate column needed — the equation is the same:

```
E_gpu,dynamic = E_gpu,total - E_gpu,baseline
```

---

## Conservation Invariants

GPU conservation is independent from CPU conservation. Each domain has its
own invariant chain.

### D4-Extended (cross-domain sanity check)
```
core_energy_uj + gpu_total_energy_uj <= pkg_energy_uj
```
GPU is in PKG remainder, so sum of core + GPU can never exceed PKG.
Zero violations expected. Validated by `validate_energy_chain.py --check d4-extended`.

### D5 — GPU AXIS 1A (sample coverage)
```
SUM(gpu_samples.energy_uj) ≈ gpu_total_energy_uj    (tolerance: 1%)
```
Validates that per-sample sum matches the scalar total stored on `runs`.

### D6 — GPU AXIS 1B (attribution closure)
```
gpu_total_energy_uj = gpu_dynamic_energy_uj + gpu_baseline_energy_uj
```
Exact by construction. Tolerance: 1 mJ for floating-point rounding.

### D7 — GPU AXIS 2A (functional projection, exact)
```
gpu_dynamic_energy_uj = gpu_llm_compute_energy_uj + gpu_orchestration_energy_uj
```
Exact by construction — remainder assigned to `gpu_llm_compute_energy_uj`.

### D8 — GPU AXIS 2B (phase projection, exact)
```
gpu_dynamic_energy_uj = gpu_phase_planning_uj + gpu_phase_execution_uj
                      + gpu_phase_synthesis_uj + gpu_phase_inter_uj
```
Exact by construction — remainder assigned to `gpu_phase_inter_uj`.

---

## Total System Energy

```sql
CREATE VIEW total_system_energy AS
SELECT
    run_id,
    attributed_energy_uj                              AS cpu_attributed_uj,
    COALESCE(gpu_dynamic_energy_uj, 0)                AS gpu_attributed_uj,
    attributed_energy_uj
        + COALESCE(gpu_dynamic_energy_uj, 0)          AS total_attributed_uj,
    gpu_attribution_method,
    gpu_count
FROM runs;
```

`total_attributed_uj` is the headline number for cross-platform comparison.
Confidence inherits the minimum of CPU and GPU confidence for the run.

---

## EpG Extensions

Three EpG variants on `goal_execution`:

| Column | Definition |
|--------|-----------|
| `successful_energy_uj` | Existing CPU EpG (unchanged) |
| `gpu_total_energy_uj` | GPU energy summed across goal attempts |
| `EpG_total` | `(successful_energy_uj + gpu_total_energy_uj) / successful_goals` |

`EpG_total` is the cross-platform headline metric. Computed by
`goal_execution_etl.py` extension (planned).

---

## run_quality GPU Validity

```sql
ALTER TABLE run_quality ADD COLUMN gpu_valid INTEGER DEFAULT 1;
ALTER TABLE run_quality ADD COLUMN gpu_rejection_reason TEXT;
```

| Rejection reason | Meaning |
|-----------------|---------|
| `counter_unavailable` | Driver does not expose energy counters |
| `sample_coverage_below_95pct` | gpu_samples cover < 95% of run duration |
| `temperature_throttle_detected` | GPU thermal throttling mid-run |
| `clock_change_mid_run` | DVFS event invalidates energy comparison |

---

## Method Registry

| method_id | Type | Confidence | Backend |
|-----------|------|-----------|---------|
| `gpu_rapl_pp1_v1` | MEASURED | 1.0 | MSR PP1 |
| `gpu_dynamic_baseline_v1` | CALCULATED | 0.90 | All backends |
| `gpu_attribution_exclusive_v1` | CALCULATED | 1.0 | All backends |
| `gpu_baseline_2sigma_v1` | MEASURED | 1.0 | All backends |
| `gpu_phase_alignment_v1` | INFERRED | 0.70 | All backends |
| `nvml_total_energy_v1` | MEASURED | 1.0 | NVML |
| `nvml_power_integration_v1` | MEASURED | 0.85 | NVML fallback |
| `dcgm_energy_v1` | MEASURED | 1.0 | DCGM |
| `iokit_gpu_energy_v1` | MEASURED | 0.90 | IOKit |
| `rocm_smi_energy_v1` | MEASURED | 0.85 | ROCm |---

## Serving Engine Telemetry

GPU energy measurement tells you how much power the GPU drew during a
run.
Serving engine telemetry tells you what the engine was doing during
that same window — KV cache state, request queue depth, token
throughput.
The two measurement streams are complementary and stored separately.

GPU energy rows land in `energy_samples` and `gpu_samples`.
Serving engine telemetry rows land in `serving_runtime_snapshots`.
Both are keyed by `run_id` and `attempt_id` so they can be joined for
per-run analysis.

### What is measured

Each snapshot captures two logical groups:

**KV cache state** — sampled at the adapter polling interval:

kv_capacity_tokens — total KV cache capacity in tokens
kv_occupied_tokens — tokens currently occupying the cache
kv_occupancy_fraction — occupied / capacity
kv_hit_rate_aggregate — fraction of requests served from cache
kv_num_evictions — evictions since server start


**Queue state** — sampled at the same interval:

queue_active — requests currently being processed
queue_waiting — requests waiting for a free slot
queue_completed — total requests completed since server start
queue_rejected — requests rejected due to queue overflow


### Telemetry availability by engine

| Engine | KV cache | Queue | Source |
|---|---|---|---|
| vLLM | Yes | Yes | GET /metrics (Prometheus) |
| SGLang | Yes | Yes | GET /metrics (Prometheus) |
| llama-server C++ | Yes | Partial | GET /metrics (Prometheus) |
| llama-cpp-python | No | No | /metrics returns 404 |
| Colibri | No | Yes | GET /health (JSON) |
| TRT-LLM | Yes | Yes | GET /prometheus/metrics |

Availability is discovered dynamically at adapter startup — never
assumed from engine type.
If `/metrics` returns 404, both groups are set to unavailable and no
rows are written.
This is correct behavior, not an error.

### Metric name differences across engines

SGLang 0.5.20 uses non-standard metric names that differ from the
Prometheus exposition format vLLM uses.
The adapter maps them explicitly:

sglang:cache_hit_rate → kv_hit_rate_aggregate
sglang:token_usage → kv_occupancy_fraction
sglang:num_running_reqs → queue_active
sglang:num_waiting_reqs → queue_waiting
sglang:num_requests_total → queue_completed


These mappings are inside `alems-plugin-sglang` and are versioned with
the plugin.
When SGLang changes metric names in a future release, only the plugin
needs updating — core schema is unchanged.

### Joining energy and telemetry

```sql
SELECT
  e.run_id,
  e.total_energy_uj,
  s.kv_hit_rate_aggregate,
  s.queue_completed,
  s.tokens_per_second
FROM runs e
JOIN serving_runtime_snapshots s
  ON e.run_id = s.run_id
WHERE e.provider = 'vllm_remote'
ORDER BY e.created_at DESC
LIMIT 20;
```

A high `kv_hit_rate_aggregate` with low `total_energy_uj` confirms the
cache was warm — the engine served tokens from memory rather than
recomputing attention.
A low hit rate with high energy is the cold-cache penalty that
researchers should control for across experiment repetitions.

---

## Limitations

1. **MSR PP1 no signals:** `source='msr_pp1'` samples have NULL for all
   signal columns (util, clocks, memory, temperature). Only energy_uj populated.

2. **Phase alignment proxy:** GPU phase energy (D8) is estimated from CPU
   phase fractions, not directly observed. Confidence 0.70. Will improve when
   NVML/DCGM signal columns enable direct GPU phase boundary detection.

3. **GN100 RAPL absent:** No RAPL equivalent on GB10 ARM platform. DCGM is
   the only path. This is a vendor product decision documented in
   arXiv:2605.27599 "The Energy Blind Spot."

4. **Apple IOKit requires sudo:** `powermetrics` on macOS requires elevated
   privileges. A-LEMS run on Stephen's machine must be launched with sudo
   or the IOKit backend falls back to NoneBackend.

5. **ROCm stub:** No AMD GPU hardware in lab. ROCmBackend interface is
   complete but untested. Activate and validate when AMD hardware joins.

---

## Cross-References

- `07-energy-readers-methodology.md` — CPU RAPL measurement, GPU PP1 section
- `measurement-methodology.md` — Overall A-LEMS measurement model
- arXiv:2605.27599 — "The Energy Blind Spot" (LOCO Workshop paper)

---

## ROCm Backend (AMD GPUs)

### Method ID: `rocm_smi_energy_v1`

Reads the AMD cumulative GPU energy counter through rsmi_dev_energy_count_get() and reports the counter delta over the run.

$$E_{gpu} = \Delta \text{ROCm}_{energy}$$

Status: stub. No AMD GPU is in the lab (as of 2026-06); the method is declared so that the reader can be activated when AMD hardware joins. Values from this method are not validated.

---

## GPU Idle Baseline (2 Sigma Method)

### Method ID: `gpu_baseline_2sigma_v1`

GPU idle power is measured at 10 Hz by the GPU collector during the idle period before an experiment, with the same 2 sigma method as the CPU baseline (see Idle Baseline Methodology in measurement-methodology.md). It is stored in idle_baselines.gpu_power_watts, and the GPU baseline energy of a run is

$$E_{gpu,baseline} = P_{gpu,idle} \times t_{run}$$

### Known Limitations

A stored idle value from a different thermal state can exceed a short run's own GPU power and give negative dynamic energy; such values are logged, not clamped silently (B29), and the per domain idle rules of the B33 contract replace this method for new runs once implemented.