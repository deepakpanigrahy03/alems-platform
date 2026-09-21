-- =============================================================================
-- v105_ear_engine.sql
-- Core schema migration: Energy-Aware Retry policy tables (A4, chunk 8.6)
-- =============================================================================
--
-- Creates ear_policy, ear_policy_rules, ear_decision_log.
-- These tables are core schema because goal_execution_manager.py (core)
-- writes to ear_decision_log on every retry decision when engine=ear.
-- Code lives in core/retry/ — tables must exist on every machine.
--
-- Prerequisite: v082 (failure_taxonomy must exist for FK reference at app layer).
-- Rule MSC-4: DDL only — no INSERT/UPDATE/DELETE in this file.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Table: ear_policy
-- One named EAR policy per row.
-- Referenced by name from YAML: retry_policy.ear_policy_name
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ear_policy (
    ear_policy_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_name         TEXT NOT NULL UNIQUE,
    description         TEXT,
    -- 'experiment_group' = aggregate calibration across all runs in a group (EAR v1).
    -- Future: 'per_task_category', 'per_model', 'per_provider'.
    calibration_scope   TEXT NOT NULL DEFAULT 'experiment_group',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- ---------------------------------------------------------------------------
-- Table: ear_policy_rules
-- One row per (ear_policy, failure_type) pair.
-- Populated by core/retry/ear_calibrator.py from failure_cost_profile (A3).
-- Never written by runtime hot path.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ear_policy_rules (
    rule_id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    ear_policy_id                   INTEGER NOT NULL REFERENCES ear_policy(ear_policy_id),

    -- Fine-grained failure type — free text, domain enforced at application layer (P1).
    failure_type_id                 TEXT NOT NULL,

    -- Maximum retry attempts for this failure type under this policy.
    max_attempts                    INTEGER NOT NULL CHECK(max_attempts >= 0),

    -- Remaining budget must be >= this threshold to allow retry (µJ).
    -- NULL = no budget check for this rule.
    cost_threshold_uj               REAL CHECK(cost_threshold_uj IS NULL
                                               OR cost_threshold_uj >= 0),

    -- Calibrated success probability from failure_cost_profile.recovery_success_rate.
    -- Populated by ear_calibrator.py. NULL = not yet calibrated.
    calibrated_success_prob         REAL CHECK(calibrated_success_prob IS NULL
                                               OR calibrated_success_prob BETWEEN 0 AND 1),

    -- Minimum success probability required to allow retry.
    -- NULL = no probability check for this rule.
    success_probability_threshold   REAL CHECK(success_probability_threshold IS NULL
                                               OR success_probability_threshold BETWEEN 0 AND 1),

    -- Action when all checks pass: retry / abort / fallback.
    action                          TEXT NOT NULL CHECK(action IN ('retry', 'abort', 'fallback')),

    -- Higher priority rules evaluated first when multiple rules match.
    priority                        INTEGER NOT NULL DEFAULT 0,

    created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(ear_policy_id, failure_type_id)
);

CREATE INDEX IF NOT EXISTS idx_ear_rules_policy ON ear_policy_rules(ear_policy_id);
CREATE INDEX IF NOT EXISTS idx_ear_rules_type   ON ear_policy_rules(failure_type_id);


-- ---------------------------------------------------------------------------
-- Table: ear_decision_log
-- One row per retry decision made by EARAdapter at runtime.
-- Written by core/retry/retry_adapter.py via flush_decision_log().
-- Audit trail for Paper 8 EAR vs flat policy comparison experiments.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ear_decision_log (
    decision_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER REFERENCES runs(run_id),
    attempt_id              INTEGER REFERENCES goal_attempt(attempt_id),

    -- Failure type that triggered the decision.
    failure_type_id         TEXT,

    attempt_number          INTEGER,

    -- Energy budget remaining at decision time (µJ). NULL if budget tracking disabled.
    budget_remaining_uj     REAL,

    -- Decision outcome.
    action                  TEXT NOT NULL,

    -- Reason code (e.g. 'ear_allows', 'ear_budget_exhausted', 'no_ear_rule_for_type').
    reason                  TEXT,

    -- Calibration snapshot — values used at decision time for reproducibility.
    calibration_cost_uj     REAL,
    calibration_success_prob REAL CHECK(calibration_success_prob IS NULL
                                        OR calibration_success_prob BETWEEN 0 AND 1),

    decided_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ear_log_run     ON ear_decision_log(run_id);
CREATE INDEX IF NOT EXISTS idx_ear_log_attempt ON ear_decision_log(attempt_id);
