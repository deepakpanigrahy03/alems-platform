-- =============================================================================
-- v132_phase_attribution_v3.sql
-- Phase attribution v3 (chunk 45, renumbered from v129 at merge, claude/DESIGN_45_phase_attribution.md).
-- =============================================================================
-- interrupt_samples.nice_ticks_start/end: /proc/stat nice ticks, so the CPU share
--   denominator is user+nice+system, the same as runs.cpu_fraction. Historical rows
--   keep NULL (their v3 share uses user+system, recorded in the method).
-- energy_attribution.attribution_residual_uj: signed difference between the run
--   attributed energy and the sum of phase and inter_phase estimates. Never
--   clamped. NULL when any term is unknown. Rule MSC-4: DDL only.
-- =============================================================================

ALTER TABLE interrupt_samples ADD COLUMN nice_ticks_start INTEGER;
ALTER TABLE interrupt_samples ADD COLUMN nice_ticks_end INTEGER;
ALTER TABLE energy_attribution ADD COLUMN attribution_residual_uj INTEGER;
