# A-LEMS — Improvement Scope & Technical Debt Register

**Internal document — not in mkdocs**
**Location:** `compliance/IMPROVEMENTS.md`
**Last updated:** 2026

---

## Chunk 11 — Code Quality & Logging (Low Risk, High Value)

| # | Issue | File | Fix |
|---|-------|------|-----|
| 11.1 | Replace all `print(🔍 DEBUG...)` with structured `logger.debug()` | experiment_runner.py, harness.py, agentic.py | Structured log format: `[component][operation] key=value` |
| 11.2 | Duplicate backward-compat energy conversion block | experiment_runner.py lines ~620, ~670 | Extract to `_convert_energy_sample(sample)` helper |
| 11.3 | `aggregate_run_stats` only called inside thermal block | experiment_runner.py | Move outside thermal block — always aggregate |
| 11.4 | Log format: add node, user, experiment_id to every line | logging config | `%(asctime)s \| %(levelname)s \| %(node)s \| %(user)s \| %(name)s \| %(message)s` |
| 11.5 | Always write execution phase event even if empty | agentic.py | Already fixed in Chunk 5 |
| 11.6 | Short phase fallback: if samples < 3, use duration × avg_power | phase_attribution_etl.py | Add `attribution_method = time_proportional` |
| 11.7 | Add provider field to orchestration_events | schema.py | `provider TEXT` column — local vs cloud synthesis distinction |

---

## Chunk 14 — Platform Readers & Developer Guide

| # | Issue | File | Fix |
|---|-------|------|-----|
| 14.1 | DiskReader not routed via factory | energy_engine.py | `ReaderFactory.get_disk_reader(config)` |
| 14.2 | DiskReaderABC missing from interfaces.py | interfaces.py | Add ABC — done in Chunk 12 fixes |
| 14.3 | Darwin DiskReader stub | darwin/disk_reader.py | IOKitDiskReader stub — done |
| 14.4 | Fallback DiskReader | fallback/disk_reader.py | FallbackDiskReader — done |
| 14.5 | disk_device hardcoded as "sda" | disk_reader.py | Read from hw_config.json — done |
| 14.6 | DummyCPUReader on macOS | factory.py | Replace with real macOS perf reader (kperf/Instruments) |
| 14.7 | DummyEnergyReader → IOKitPowerReader | Chunk 1.1 | Real IOKit implementation |
| 14.8 | EnergyEstimator zeros → ML model | Chunk 1.2 | After Chunk 7 model factory |
| 14.9 | Developer deep-dive guide | docs/ | Done — 14-hardware-readers-developer-guide.md |
| 14.10 | AMD perf event aliases | perf_reader.py | Add r0151 (L1d), r0f24 (L2) to DEFAULT_EVENTS |
| 14.11 | frequency_mhz range [400,6000] fails on idle | test_runs_regression_extended.sh | Widen to [50, 6000] — done |
| 14.12 | phase closure ETL timing in extended regression | test_runs_regression_extended.sh | Add ETL call before check — done |
| 14.13 | 30% comments missing in disk_reader.py | disk_reader.py | Add inline comments |
| 14.14 | Docstrings missing in aggregate_hardware_metrics.py | aggregate_hardware_metrics.py | Add docstrings |

---

## Chunk 1.1 — IOKit macOS (Needs Mac Hardware)

| # | Issue | Fix |
|---|-------|-----|
| 1.1.1 | IOKitPowerReader returns zeros | Real IOKit energy read via `IOPMCopyCPUPowerStatus` |
| 1.1.2 | macOS DiskReader returns None | `IOBlockStorageDriver` statistics |
| 1.1.3 | macOS SensorReader returns None | SMC temperature/voltage/fan via `SMCKit` |
| 1.1.4 | macOS PerfReader returns None | `kperf` / `kdebug` PMU counters |

---

## Chunk 1.2 — ARM ML Estimation (Needs Chunk 7 Model)

| # | Issue | Fix |
|---|-------|-----|
| 1.2.1 | EnergyEstimator returns zeros | Train XGBoost on x86 data, infer on ARM |
| 1.2.2 | attributed_energy_uj = 0 on ARM | Flows from energy estimation |

---

## Chunk 7 — Model Factory + Adapters

| # | Component | Status |
|---|-----------|--------|
| 7.1 | Model factory routing | Not started |
| 7.2 | 8 LLM adapters | Not started |
| 7.3 | Provider field in orchestration_events | Blocked by Chunk 7 |

---

## Architecture Observations (No Chunk Assigned)

| # | Observation | Severity | Notes |
|---|-------------|----------|-------|
| A.1 | `sqlite_adapter.py` insert_energy/cpu/interrupt never called — dead code | Low | Delete in Chunk 11 cleanup |
| A.2 | Two duplicate `import os` in harness.py (lines 41, 78) | Low | Remove duplicate |
| A.3 | `_read_cpu_ticks` called twice per sample in energy_engine (old double-increment bug comment) | Medium | Verify fixed |
| A.4 | `perf_reader.py` uses subprocess for perf stat — 100 subprocesses/second at 10Hz would be too heavy, but currently called once per run | OK | Document clearly |
| A.5 | `run_metrics_agg` / `telemetry_events` architecture (Chunk 12/13) deferred — runs table will keep growing | Medium | Revisit after Chunk 7 |
| A.6 | No GPU support yet | Future | Chunk 15: NVML for NVIDIA, ROCm for AMD |
| A.7 | No multi-process PID tracking | Medium | Only top-level PID tracked — subprocesses not attributed |
| A.8 | `synthesis_energy = 0` for all local provider runs | Expected | 1-2ms synthesis, no samples in window |
| A.9 | `voltage_vcore = NULL` on ThinkPad | Expected | Not exposed via hwmon on laptops |
| A.10 | `l1d_cache_misses = 0` on ThinkPad Intel | Expected | PMU event not available on this CPU model |

---

## Data Quality Issues (Backfill Needed)

| Run Range | Issue | Cause | Can Backfill? |
|-----------|-------|-------|---------------|
| < run 1837 | cpu_fraction = NULL | Pre-Chunk 3 | ❌ No /proc data |
| < run 1849 | execution_energy = 0 | No execution phase event | ❌ No orchestration event |
| < run 1853 | io_samples empty | Pre-Chunk 12 | ❌ No disk reader |
| < run 1853 | l2/l3 cache = NULL | Pre-Chunk 12 | ❌ No perf cache events |
| All runs | voltage_vcore = NULL | ThinkPad hardware | ❌ Not available |
| All local runs | synthesis_energy = 0 | 1ms synthesis | ❌ Physics limit |

---

## Performance Observations

| Component | Current | Concern | Fix |
|-----------|---------|---------|-----|
| Phase ETL | async thread | Runs after save_pair — regression needs explicit trigger | Add to test suite |
| Hardware ETL | async thread | Same as above | Done in regression fix |
| disk_reader | 10Hz sampling | zram detection fixed | Monitor on new hardware |
| perf_reader | once per run | Subprocess overhead acceptable | OK |
| thermal_reader | 1Hz | Appropriate for thermal | OK |

## IMP-RECORD-01: Optional cool-down wait between repetitions
Pause an experiment between repetitions until the chip is back near its starting temperature, so every repetition starts from the same thermal state. Changes experiment duration; not part of continuous recording.

## IMP-RECORD-02: Fair split of shared energy between concurrent workloads
Attribute machine-level readings between overlapping experiments (for example by per-process GPU and CPU use).

## IMP-SPANS-01: Validate agent spans at write time
Route `SpanWriter` through the vocabulary descriptor so unknown kinds, wrong parents and bad attributes are refused for every vocabulary, not only recording spans.

## IMP-MIGRATE-01: Show extension migrations in --plan
Make `cmd_plan` list pending extension migrations as `cmd_migrate` applies them, so the plan step shows every change before `--run`.

## IMP-RECORD-03: GPU process checkpoint at run start without delay
Take the last process-list checkpoint just before a run without delaying the run start (for example a snapshot already in flight when the pause arrives), so the checkpoint before a run is not up to the check interval old.

## IMP-RECORD-04: GPU power between runs
Record GPU power between runs (and inside idle windows) so machines whose main channel is GPU power get a matched idle power.

## IMP-SENSORS-01: Thermal safety guard from discovered limits
The catalog records per-sensor limits: on GB10 NVML reports GPU slowdown 86 C, shutdown 90 C, max operating 99 C (shutdown below max operating, stored as reported). The load guard required before the session runner should stop at a margin below the discovered slowdown limit instead of a typed number.

## IMP-SENSORS-02: Record catalog-only sensors
Add a per-sensor recording path for hwmon inputs and Mac helper sensors so that profiles on x86, AMD and Mac can name them.

## IMP-GUARD-01: Start level from the measured idle headroom
Replace the provisional start floor with `idle headroom - k` once loaded-idle repeatability is measured (the floor was lowered from 28 to 20 C because GB10 idles near 70 C with the model server loaded).

## IMP-WRITER-01: Writer turn timers
Record how long writers wait for and hold the store turn, and warn on long holds, so long transactions can be found.
