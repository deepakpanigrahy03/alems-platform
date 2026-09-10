-- v083: Add measurement_source to cpu_samples
-- Root cause: cpu_samples is written by three mechanisms (turbostat
-- continuous rows, ARM PMU summary row, Darwin summary row), soon a
-- fourth (SysfsCPUReader fallback per BUG-08), but no column recorded
-- which one wrote a given row. cpu_idle_states already solved this
-- correctly (measurement_source column) — this brings cpu_samples
-- into alignment.
-- Applies to: ALL platforms

ALTER TABLE cpu_samples ADD COLUMN measurement_source TEXT;


