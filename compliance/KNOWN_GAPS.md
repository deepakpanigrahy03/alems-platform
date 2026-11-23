# A-LEMS Known Gaps — Fresh Checkout Issues
# These gaps cause silent failures on new machines.
# All are tracked for resolution in the installer script (install.sh).

---

## Gap 1: HuggingFace benchmark cache missing on fresh checkout

**Impact:** `humaneval_t1_*` and `triviaqa_t1_*` tasks fail silently on first run.
**Root cause:** `data/benchmarks/` is not gitignored globally — only specific DB files are.
Benchmark cache is machine-local. Fresh checkout has no cache.
**Symptom:** Task runs but `prompt` is None → LLM gets empty input → wrong answer scored.
**Fix:** `install.sh` must run:
```bash
python scripts/prefetch_benchmarks.py --datasets gsm8k,triviaqa,humaneval
```
**HuggingFace auth:** `gsm8k` and `triviaqa` — public, no token needed.
`humaneval` — requires HF token + license acceptance at https://huggingface.co/datasets/openai_humaneval
```bash
huggingface-cli login   # paste token with read access
```
**Status:** Not documented anywhere. `benchmark_loader.py` has zero auth handling.

---

## Gap 2: `perf_event_paranoid` not set on fresh checkout

**Impact:** `PerfReader.perf_available = False` → all IPC/cache columns NULL.
**Root cause:** Default Linux kernel sets `/proc/sys/kernel/perf_event_paranoid = 3`
(restricted). A-LEMS needs ≤ 1 for process-level perf counters.
**Fix:** `install.sh` must run:
```bash
echo 1 | sudo tee /proc/sys/kernel/perf_event_paranoid
echo 'kernel.perf_event_paranoid = 1' | sudo tee -a /etc/sysctl.d/99-alems.conf
sudo sysctl -p /etc/sysctl.d/99-alems.conf
```
**Status:** Not in any setup doc.

---

## Gap 3: turbostat capabilities not set on fresh checkout

**Impact:** `package_temp = NULL`, C-state residency = NULL for all runs.
**Root cause:** turbostat binary needs `cap_sys_rawio` capability.
Fresh checkout has no systemd unit installed.
**Fix:** `install.sh` must run:
```bash
sudo bash scripts/fix_permissions.sh
sudo systemctl enable alems-turbostat-caps.service
sudo systemctl start alems-turbostat-caps.service
```
**Status:** Documented in admin guide (doc 29) but not in install.sh.

---

## Gap 4: `detect_hardware.py` must run on first boot

**Impact:** `hw_config.json` missing → energy_engine falls back to defaults → wrong thermal zone mapping.
**Fix:** `install.sh` must run:
```bash
python scripts/detect_hardware.py
```
**Note:** `hw_config.json` is machine-generated — never commit to git.
**Status:** Mentioned in admin guide but not enforced by installer.

---

## Gap 5: RAPL permissions not set on fresh checkout

**Impact:** `pkg_energy_uj = NULL` → no energy measurement → all EpG values NULL.
**Fix:** `install.sh` must run:
```bash
sudo systemctl enable alems-rapl-permissions.service
sudo systemctl start alems-rapl-permissions.service
```
**Status:** systemd unit exists but not documented in install flow.

---

## Gap 6: `data/benchmarks/` not pre-seeded

**Impact:** First run of any Tier 1 benchmark task downloads dataset — adds
10-60 seconds to first experiment, pollutes timing measurements.
**Fix:** Prefetch all datasets before first experiment run.
**Status:** No prefetch script exists yet. `benchmark_loader.py` caches on first use.

---

## Resolution Plan

All gaps resolved by `scripts/install.sh` (to be created in installer chunk).
Order of operations:
1. `pip install -r requirements.txt`
2. Set RAPL permissions (Gap 5)
3. Set perf_event_paranoid (Gap 2)
4. Set turbostat capabilities (Gap 3)
5. Run detect_hardware.py (Gap 4)
6. HuggingFace login + prefetch benchmarks (Gaps 1, 6)
7. Run migrations
8. Verify: `bash scripts/test_provenance.sh`

## KG-PROVENANCE-01: 4 methods missing description in measurement_method_registry
Methods: arm_thermal_sysfs_v1, gpu_attribution_exclusive_v1, gpu_baseline_2sigma_v1, gpu_phase_alignment_v1
Cause: doc/section fields missing or pointing to wrong file in seed_methodology.py
Impact: test_provenance.sh FAIL on "Methods missing description"
Status: pre-existing, not introduced by outlier detection work

## KG-PROVENANCE-02: metric_display_registry empty on GN100
Cause: migrate_yaml_to_db.py fails on GN100 due to query_registry missing source_yaml column
Root cause: schema drift between GN100 and UBUNTU2505, source_yaml column exists on UBUNTU2505 but not GN100
Impact: test_provenance.sh FAIL on "Display registry rows"
Fix: apply missing ALTER TABLE for source_yaml on GN100, then re-run migrate_yaml_to_db.py
Status: pre-existing, separate from outlier detection work

## KG-SAMPLING-01: most samplers run outside the sampling policy
Tables outside config/sampling.yaml (rate declared nowhere, achieved rate never checked): power_rail_samples, cpu_samples, interrupt_samples, io_samples, nic_samples, legacy energy_samples, legacy thermal_samples.
Under the policy: energy_samples_v2 (RAPL, SPBM, IOKIT), gpu_samples (GPU_COLLECTOR), thermal_samples_v2 (THERMAL_ZONE, policy v2).
Impact: a slowed or paused sampler in these tables is not flagged by invariant S2.
Fix: declare each source kind in the policy and write a sampler_stats row per run (own work package; core run path).
Status: found 2026-11-18, not fixed.

## KG-THERMAL-01: thermal quality flags collapse to MISSING
read_all_thermal() returns None for every non-VALID zone; insert_thermal_samples_v2() stores every None as MISSING / NULL_FROM_READER with temp_celsius 0.0.
Impact: READ_FAILED, OUT_OF_RANGE and MISSING cannot be told apart in thermal_samples_v2; the out-of-range value is lost. 0.0 C is the documented marker for "no reading" (FAD-2: thermally impossible); every consumer filters quality_flag = 'VALID'.
Status: found 2026-11-18, not fixed; relevant to per-sensor validity checks in session quality checks.

## KG-THERMAL-02: cooling is a single end-of-run snapshot
cooling_samples is written once per run (snapshot_cooling_state), not sampled continuously. The COOLING source in sampling policy v2 (1 Hz) applies to continuous session recording, not to normal runs, which therefore have no COOLING sampler_stats row.
Status: by design until session recording.

## KG-SCHEMA-01: extension table DDL in core schema.py
energy_validation_dcgm_coverage is defined in core/database/schema.py and created by sqlite_adapter.create_tables() on every store, although it is a namespace table (CH39-5, MSC-5: extension migration plus namespace create path). thermal_exposure keeps its tables only in its extension migration.
Impact: the table appears on stores where energy_validation is inactive; create_tables() on a store copy adds it.
Status: found 2026-11-18; owner energy_validation.

## KG-RECORD-01: Short gaps at run boundaries (continuous recording)
The recorder pauses at a run's start notification and resumes after the run's measurement stops; the milliseconds the engine needs to start and stop its own samplers are covered by neither. Measured: longest between-runs thermal gap 89 ms at 20 Hz (limit 100 ms). A run that raises between start and stop leaves the recording paused until the next run ends (a reported gap, never wrong data).

## KG-RECORD-02: No CLI command for continuous recording
The minimal command is `scripts/tools/thermal_record.py`; an `alems` subcommand needs core CLI registration and is left to the experiment runner work.

## KG-RECORD-03: Overlapping experiments record duplicates
Two opted-in experiments on one machine each store their own copy of the same machine readings, reflecting both workloads. No lock; to be handled with the sampler consolidation (KG-SAMPLING-01).

## KG-RECORD-04: Recording spans from the session runner not yet written
The `thermal_session` vocabulary defines warmup, post idle window, goal_slot, pause and calibration_step; only a session runner produces them. The recorder writes session, pre idle window, workload, run_slot, gap and cooling_tail.

## KG-RECORD-05: Recording placements use the node name 'local'
`open_recording()` passes no node name, so every placement uses the recorder default `local` (logical, never a hostname). Multi-node analysis needs a configured logical node name.
