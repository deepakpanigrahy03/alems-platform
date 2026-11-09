-- s021_rail_domain_energy_units.sql
-- Rail domains 25 to 29 relabelled as energy per sample interval (uJ).
-- Runs on every machine via alems dev migrate --run.
-- Data only (MSC-4/MSC-6). Idempotent: rerun sets the same values.
--
-- Why: rail domains 25 to 29 were seeded by s002 as instantaneous power (mW).
-- Since commit 9764052 the energy collector stores each rail as energy per
-- sample interval: power (mW) x measured interval (ns) / 1e6 = uJ
-- (core/readers/energy_collector.py, _tick). s002 uses INSERT OR IGNORE, so
-- it cannot correct existing rows; this UPDATE gives fresh and migrated
-- stores the same catalog.
--
-- Limitation: stores whose rail rows were written before 9764052 hold mW in
-- energy_sample_domains for these domains. This file changes labels only,
-- never stored sample values. Analyses of such stores must use the store's
-- creation commit (environment_config.git_commit), not this label.
--
-- Not changed here: DLA (20), pending a decision on is_cumulative
-- (core/attribution/energy_window.py filters on is_cumulative = 1).

UPDATE energy_domains
SET unit = 'uj',
    is_cumulative = 0,
    description = 'SPBM soc_pkg rail, energy per sample interval (power x measured interval), sub-rail of package boundary'
WHERE domain_id = 25 AND name = 'SOC_PKG';

UPDATE energy_domains
SET unit = 'uj',
    is_cumulative = 0,
    description = 'SPBM cpu_gpu combined rail, energy per sample interval (power x measured interval), sub-rail of package boundary'
WHERE domain_id = 26 AND name = 'CPU_GPU';

UPDATE energy_domains
SET unit = 'uj',
    is_cumulative = 0,
    description = 'SPBM vcore rail, energy per sample interval (power x measured interval); not additive with CPU rails'
WHERE domain_id = 27 AND name = 'VCORE';

UPDATE energy_domains
SET unit = 'uj',
    is_cumulative = 0,
    description = 'SPBM dc_input rail, energy per sample interval (power x measured interval); physical boundary undocumented'
WHERE domain_id = 28 AND name = 'DC_INPUT';

UPDATE energy_domains
SET unit = 'uj',
    is_cumulative = 0,
    description = 'SPBM prereg rail, energy per sample interval (power x measured interval); not additive with CPU rails'
WHERE domain_id = 29 AND name = 'PREREG';
