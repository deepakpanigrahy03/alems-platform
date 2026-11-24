-- =============================================================================
-- energy_validation / e002_create_engine_provenance.sql
-- Extension migration: serving instance per experiment
-- =============================================================================
--
-- Runs only where energy_validation is active (applied by alems dev migrate).
--
-- One row per (experiment, serving instance). A restart during an experiment
-- gives a second row. Written at persistence time on every execution path
-- (save_pair, save_single, execute_goal), only for providers whose base_url
-- points at this machine.
-- Method: engine_instance_match_v1 (SYSTEM).
--
-- The listener on the provider port is identified by PID and process start
-- time and matched to the ai-stack provenance JSON (schema
-- ai_stack_provenance_v2). match_method says how: pid_start_time,
-- listener_only_no_record, no_listener. Missing facts are NULL (MIC-1).
-- worker_association_status: confirmed, inferred, unknown; analyses needing
-- the GPU process must use confirmed only.
-- command_json is redacted; command_sha256 hashes the redacted argv.
--
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_engine_provenance (
    provenance_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exp_id                     INTEGER NOT NULL REFERENCES experiments(exp_id),
    engine                     TEXT,
    engine_version             TEXT,
    server_pid                 INTEGER,
    server_start_utc           TEXT,
    port                       INTEGER NOT NULL,
    model_id                   TEXT,
    command_json               TEXT,
    command_sha256             TEXT,
    source_path                TEXT,
    source_sha256              TEXT,
    match_method               TEXT    NOT NULL CHECK(match_method IN ('pid_start_time', 'listener_only_no_record', 'no_listener')),
    server_instance_id         TEXT,
    worker_pids_json           TEXT,
    listener_identified        INTEGER NOT NULL CHECK(listener_identified IN (0, 1)),
    worker_identified          INTEGER NOT NULL CHECK(worker_identified IN (0, 1)),
    worker_association_method  TEXT,
    worker_association_status  TEXT    NOT NULL CHECK(worker_association_status IN ('confirmed', 'inferred', 'unknown')),
    method_id                  TEXT    NOT NULL DEFAULT 'engine_instance_match_v1',
    created_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(exp_id, server_instance_id, match_method)
);
CREATE INDEX IF NOT EXISTS idx_ev_engine_prov_exp ON energy_validation_engine_provenance(exp_id);
