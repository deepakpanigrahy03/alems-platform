-- =============================================================================
-- energy_validation / e005_create_sequencer.sql
-- Extension migration: step sequencer (sessions, planned steps, attempts,
-- attempt to run links, kind specific results, bracket eligibility)
-- =============================================================================
--
-- Guarantee: every committed sequenced run belongs to exactly one durable
-- attempt. The attempt row is committed before dispatch; the run to attempt
-- link is written in the same transaction as the run row (persist_raw).
-- Execution status and comparison eligibility are separate records.
-- Clock: *_mono_ns = time.monotonic_ns() of the sequencer process,
-- *_utc_ns = time.time_ns() (same wall clock as energy and GPU samples).
--
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_seq_sessions (
    session_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT,
    plan_path             TEXT,
    plan_source_sha256    TEXT    NOT NULL,
    resolved_plan_json    TEXT    NOT NULL,
    resolved_plan_sha256  TEXT    NOT NULL,
    plan_schema_version   INTEGER NOT NULL,
    seed                  INTEGER,
    realized_order_json   TEXT,
    versions_json         TEXT,
    config_layers_json    TEXT,
    status                TEXT    NOT NULL CHECK(status IN ('running', 'completed', 'stopped', 'interrupted')),
    stop_reason           TEXT,
    start_utc_ns          INTEGER NOT NULL,
    end_utc_ns            INTEGER,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS energy_validation_seq_steps (
    step_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES energy_validation_seq_sessions(session_id),
    cycle_id         INTEGER NOT NULL,
    bracket_id       TEXT    NOT NULL,
    condition        TEXT    NOT NULL,
    role             TEXT    NOT NULL CHECK(role IN ('pre', 'treatment', 'post', 'recovery')),
    kind             TEXT    NOT NULL,
    params_json      TEXT    NOT NULL,
    planned_index    INTEGER NOT NULL,
    UNIQUE(session_id, planned_index),
    UNIQUE(session_id, bracket_id, role)
);

CREATE TABLE IF NOT EXISTS energy_validation_seq_attempts (
    attempt_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id             INTEGER NOT NULL REFERENCES energy_validation_seq_steps(step_id),
    attempt_no          INTEGER NOT NULL,
    execution_status    TEXT    NOT NULL CHECK(execution_status IN
                          ('running', 'completed', 'failed', 'skipped', 'interrupted')),
    reason_code         TEXT,
    reason_detail       TEXT,
    settle_start_mono_ns INTEGER,
    settle_end_mono_ns   INTEGER,
    gate_json           TEXT,
    exec_start_mono_ns  INTEGER,
    exec_end_mono_ns    INTEGER,
    exec_start_utc_ns   INTEGER,
    exec_end_utc_ns     INTEGER,
    meas_start_utc_ns   INTEGER,
    meas_end_utc_ns     INTEGER,
    clock_domain        TEXT    NOT NULL DEFAULT 'host_monotonic+wall',
    baseline_id         TEXT,
    exp_id              INTEGER,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(step_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS energy_validation_seq_attempt_runs (
    attempt_id      INTEGER NOT NULL REFERENCES energy_validation_seq_attempts(attempt_id),
    run_id          INTEGER NOT NULL UNIQUE REFERENCES runs(run_id),
    child_seq       INTEGER NOT NULL,
    rep_num         INTEGER,
    workflow_type   TEXT,
    UNIQUE(attempt_id, child_seq)
);
CREATE INDEX IF NOT EXISTS idx_ev_seq_ar_attempt ON energy_validation_seq_attempt_runs(attempt_id);

CREATE TABLE IF NOT EXISTS energy_validation_seq_step_results (
    attempt_id  INTEGER NOT NULL REFERENCES energy_validation_seq_attempts(attempt_id),
    key         TEXT    NOT NULL,
    value       TEXT,
    unit        TEXT,
    PRIMARY KEY (attempt_id, key)
);

CREATE TABLE IF NOT EXISTS energy_validation_seq_bracket_eligibility (
    session_id    INTEGER NOT NULL REFERENCES energy_validation_seq_sessions(session_id),
    bracket_id    TEXT    NOT NULL,
    domain        TEXT    NOT NULL,
    eligible      INTEGER NOT NULL CHECK(eligible IN (0, 1)),
    reason        TEXT,
    rule_version  TEXT    NOT NULL,
    PRIMARY KEY (session_id, bracket_id, domain)
);
