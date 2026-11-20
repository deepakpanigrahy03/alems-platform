## EEI-2 Violation: _insert_nic_samples
File: core/execution/experiment_runner.py line 64
Issue: Uses db.db.conn.executemany directly instead of repository layer.
Fix: Move to core/database/repositories/samples.py as insert_nic_samples()
     and call via db.insert_nic_samples() like insert_cpu_samples.
Priority: Before next major release. Not a paper blocker.

## TD-7: cpuidle dual-mechanism, investigated and resolved (not a collision)
Files: core/readers/sysfs_cpu_reader.py (untracked, in progress),
       core/database/repositories/thermal.py write_from_cpuidle_sysfs()
Issue: Two independent mechanisms both write to cpu_idle_states using
       the literal source tag "cpuidle_sysfs" — SysfsCPUReader (new,
       x86 quality-fallback, per-tick delta sampling) and
       write_from_cpuidle_sysfs() (existing, ARM primary path,
       cumulative snapshot). Initially suspected as a duplicate-
       implementation collision (TD-1 through TD-6 pattern).
Resolution: Confirmed via real data (GN100, AMD) that these serve
       different platforms under different trigger conditions —
       ARM has no turbostat at all; x86 uses turbostat by default
       and only falls back on detected degradation (see BUG-08).
       Not a true collision. No fix required. Logged for future
       sessions so this isn't re-investigated from scratch.
Priority: Closed, informational only.

## TD-8: insert_cpu_samples has five implementations, only one live
Files: core/database/repositories/samples.py (LIVE — called via
       DatabaseManager.insert_cpu_samples() -> self.samples.insert_cpu_samples()),
       core/database/manager.py (delegates to samples.py, confirmed live),
       core/database/base.py (unused for this call chain, confirmed dead),
       core/database/sqlite_adapter.py (unused for this call chain, confirmed dead),
       core/database/manager_oldversion.py (_insert_cpu_samples, dead — filename
       confirms legacy)
Issue: Four dead/duplicate implementations of the same method exist
       alongside the one real path, discovered while wiring the
       measurement_source column fix. Wasted recon time this session
       confirming which was real before any edit could be trusted.
Fix: Remove the three confirmed-dead implementations (base.py,
     sqlite_adapter.py, manager_oldversion.py) in a dedicated cleanup
     pass. Confirm no other repository method has the same duplication
     before removing, since these classes may still be live for other
     methods even if dead for this one.
Priority: Before next major release. Not a paper blocker.

## TD: thermal loop scheduling (fixed 2026-11-18)
The energy engine thermal loop scheduled each sample from the actual time of the previous one (drift accumulated) and an exception in a read ended the thread silently. Fixed: fixed-step schedule on the monotonic clock, failed reads counted and logged, THERMAL_ZONE sampler_stats row per run.

## TD-RECORD-01: experiment_type value 'thermal_session' unused
Core migration v123 allows `experiment_type = 'thermal_session'`; the final recording design stores no experiment or run row for a recording, so the value is unused. Kept by decision (harmless, tested).

## TD-RECORD-02: Recorder keeps its own GPU monitoring handle during runs
The recorder's DCGM/NVML backend stays open while paused. Reads stop during a run, but the handle is released only at close.

## TD-SPANS-01: Older vocabularies not on the span vocabulary descriptor
`alems_sdk.spans.SpanVocabulary` declares kinds, parents, placement and typed attributes and validates spans. The `agent` (class) and `generic` (module) vocabularies predate it, are not validated when written, and `GROUP_CONTRACT["alems.spans.vocabularies"]` stays `None` because a class contract would reject both in the conformance kit. Move both onto the descriptor and add a descriptor contract kind to the kit.

## TD-RECORD-03: Recorder keeps unused cool-down state
`ExperimentRecorder._pre_temps` and `_recent_temps` (filled by `_track_temp`) served the old all-zones cool-down rule, now replaced by `idle_session`. They are still filled; remove with the next recorder change.

## TD-RECORD-04: Dev store column type differs from the migration file
The worktree dev store applied `e003` before `te_session.idle_baseline_id` was corrected from INTEGER to TEXT (file not yet committed). The dev store keeps an INTEGER-declared column (text ids are stored correctly by SQLite affinity); fresh stores get TEXT.

## TD-IDLE-01: Counter wraparound range not passed to the idle power rule
`idle_power_from_counter_v1` corrects a wraparound only when the counter range is known; the recorder does not yet pass `max_energy_range_uj`, so a wrapped interval is dropped (never guessed).

## TD-SENSORS-01: Two discovery paths for thermal zones
`scripts/detect_hardware.py` (no venv, writes hw_config thermal paths) and `core/thermal/thermal_discovery.py` / `core/thermal/thermal_descriptors.py` (venv) each walk sysfs. They agree today; merge once install runs detection inside the venv.

## TD-SENSORS-02: Conformance kit does not run descriptor validation
`alems_sdk.thermal_catalog.validate_descriptor` is applied by `scripts/detect_thermal.py` and by tests, not yet by the SDK conformance kit for third-party thermal readers.
