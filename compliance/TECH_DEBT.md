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
