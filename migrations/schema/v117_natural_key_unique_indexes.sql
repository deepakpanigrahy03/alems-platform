-- v117_natural_key_unique_indexes.sql
-- INV-D1 enforced by the store: UNIQUE indexes on the declared natural keys
-- (core/validation/persistence_keys.yaml) of the tables where duplicate
-- writes occurred (G82, G98, G99). A future double write fails at insert
-- time instead of landing silently.
-- Precondition: no duplicates. Run alems validate persistence --repair --yes
-- first; on a store with duplicates this migration fails with
-- "UNIQUE constraint failed" and nothing is applied.
-- Rows with a NULL key column are not covered (SQLite treats NULLs as
-- distinct); alems validate persistence still checks them.
-- Additive only: indexes, no table changes (SC-5).

CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_energy_samples_v2
    ON energy_samples_v2(run_id, source_id, timestamp_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_interrupt_samples
    ON interrupt_samples(run_id, timestamp_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_io_samples
    ON io_samples(run_id, device, sample_start_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_thermal_samples
    ON thermal_samples(run_id, timestamp_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_energy_derived_metrics
    ON energy_derived_metrics(run_id, sample_id, metric_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_llm_interactions
    ON llm_interactions(run_id, step_index, request_start_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_orchestration_events
    ON orchestration_events(run_id, step_index, phase, event_type, start_time_ns);

INSERT INTO schema_version (version, applied_at, description)
VALUES (117, datetime('now'), 'natural key unique indexes: INV-D1 enforced on sample, interaction and event tables');
