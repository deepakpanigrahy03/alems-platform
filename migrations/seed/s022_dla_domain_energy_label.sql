-- s022_dla_domain_energy_label.sql
-- DLA (20) relabelled as energy per sample interval, matching its fill path.
-- Runs on every machine via alems dev migrate --run.
-- Data only (MSC-4/MSC-6). Idempotent: rerun sets the same values.
--
-- Why: DLA is filled exactly like the SPBM rails 25 to 29 (power x measured
-- interval per tick, core/readers/energy_collector.py), so it is not a
-- cumulative counter. s002 labelled it is_cumulative = 1.
--
-- Effect on attribution: core/attribution/energy_window.py selects the
-- primary domain among is_cumulative = 1 domains. With a root sample present
-- (PACKAGE on every platform that fills DLA) the root query decides and DLA
-- is never chosen, so derived values do not change. In the child fallback,
-- DLA can no longer be picked as a primary counter, which is the intent.
--
-- Unchanged: unit (already uj), reader_keys (NULL; idle baseline check only).

UPDATE energy_domains
SET is_cumulative = 0,
    unit = 'uj',
    description = 'Deep Learning Accelerator SPBM rail, energy per sample interval (power x measured interval)'
WHERE domain_id = 20 AND name = 'DLA';
