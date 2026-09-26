-- v114_span_id_columns.sql
-- Adds nullable span_id to existing tables so new rows carry their span reference.
-- Existing rows stay NULL; no data is touched (Rule S, additive schema only).
-- Also adds fidelity and error_bound to energy_sources (spec section 5).

ALTER TABLE runs               ADD COLUMN span_id TEXT;
ALTER TABLE goal_execution     ADD COLUMN span_id TEXT;
ALTER TABLE goal_attempt       ADD COLUMN span_id TEXT;
ALTER TABLE orchestration_events ADD COLUMN span_id TEXT;
ALTER TABLE llm_interactions   ADD COLUMN span_id TEXT;
ALTER TABLE tool_failure_events ADD COLUMN span_id TEXT;

-- energy_sources: fidelity mirrors reader FIDELITY class attribute (MEASURED/INFERRED/LIMITED).
-- error_bound is non-null only for INFERRED sources; stored as a descriptive string e.g. "±30%".
-- Default MEASURED preserves existing rows (all 5 current sources are MEASURED).
ALTER TABLE energy_sources ADD COLUMN fidelity    TEXT NOT NULL DEFAULT 'MEASURED'
    CHECK (fidelity IN ('MEASURED','INFERRED','LIMITED'));
ALTER TABLE energy_sources ADD COLUMN error_bound TEXT;

-- attribution_residual: populated after each run by legacy_v1 in 4b; created here as plumbing.
-- All columns nullable so partial writes during 4b development do not corrupt rows.
CREATE TABLE IF NOT EXISTS attribution_residual (
    residual_id      INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL,
    domain           TEXT    NOT NULL,               -- matches energy_sources.name or 'pkg_total'
    window_label     TEXT    NOT NULL,               -- 'task'|'pre_task'|'post_task'
    measured_uj      BIGINT,
    attributed_uj    BIGINT,
    residual_uj      BIGINT,
    tolerance_uj     BIGINT,                         -- declared meter resolution + noise
    status           TEXT    CHECK (status IN ('ok','large','negative','unmeasured')),
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_residual_run ON attribution_residual(run_id);

INSERT INTO schema_version (version, applied_at, description)
VALUES (114, datetime('now'), 'Nullable span_id on runs/goal_execution/goal_attempt/orchestration_events/llm_interactions/tool_failure_events; fidelity+error_bound on energy_sources; attribution_residual table');
