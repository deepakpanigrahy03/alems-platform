-- =============================================================================
-- ext-failure-profiling / v90002_ext_failure_cost_profile.sql
-- Extension migration: Per-Failure-Type Cost Profiling tables
-- =============================================================================
--
-- Creates the failure_cost_profile table and companion views for the
-- ext-failure-profiling research extension (A3, chunk 8.6).
--
-- This migration runs ONLY on machines where ext-failure-profiling is listed
-- in [extensions] active in app_settings.yaml.
--
-- Capability added:
--   Automated computation of recovery cost and success statistics per failure
--   type and experiment group.
--   After this migration, running cost profiling on new failure types requires
--   one ETL call and zero code changes.
--
-- Tables created:
--   failure_cost_profile  — aggregated cost statistics per (failure_type, group)
--
-- Views created:
--   v_failure_cost_comparison      — ranks types by cost_per_recovery_success
--   v_failure_cost_by_experiment   — cross-experiment breakdown per model/provider
--
-- All foreign keys reference core tables (always present).
-- failure_type_id references failure_taxonomy at application layer only
-- (same pattern as tool_failure_events per A1/Bug-13 design rule).
--
-- Rule MSC-4: DDL only in this file (no INSERT/UPDATE/DELETE).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Table: failure_cost_profile
--
-- One row per (failure_type_id, experiment_group) pair.
-- Populated exclusively by scripts/etl/failure_cost_profile_etl.py via
-- INSERT OR REPLACE — never written by the runtime hot path.
-- All energy values stored in microjoules (µJ) matching core table convention.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS failure_cost_profile (
    profile_id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Fine-grained failure type from failure_taxonomy.
    -- Free text — domain vocabulary enforced at application layer (P1, INV-3).
    failure_type_id             TEXT NOT NULL,

    -- Identifies the experiment group these statistics were computed from.
    -- Matches experiments.group_id.
    experiment_group            TEXT NOT NULL,

    -- Number of failure events that contributed to this profile row.
    sample_count                INTEGER NOT NULL CHECK(sample_count >= 0),

    -- Recovery cost distribution in µJ.
    -- NULL when sample_count = 0 (type observed but no energy data available).
    recovery_cost_uj_mean       REAL CHECK(recovery_cost_uj_mean   IS NULL OR recovery_cost_uj_mean   >= 0),
    recovery_cost_uj_median     REAL CHECK(recovery_cost_uj_median IS NULL OR recovery_cost_uj_median >= 0),
    recovery_cost_uj_p25        REAL,
    recovery_cost_uj_p75        REAL,
    recovery_cost_uj_std        REAL CHECK(recovery_cost_uj_std    IS NULL OR recovery_cost_uj_std    >= 0),

    -- Fraction of failures where the immediately following attempt succeeded.
    -- Definition: ga_next.attempt_number = ga.attempt_number + 1
    --             AND ga_next.outcome = 'success'.
    recovery_success_rate       REAL CHECK(recovery_success_rate   IS NULL OR recovery_success_rate   BETWEEN 0 AND 1),

    -- Raw count of successful recoveries (numerator of recovery_success_rate).
    recovery_success_count      INTEGER CHECK(recovery_success_count IS NULL OR recovery_success_count >= 0),

    -- Mean retry attempts before terminal outcome (success or retries exhausted).
    recovery_attempts_mean      REAL,

    -- Mean wall-clock recovery latency in milliseconds.
    recovery_latency_ms_mean    REAL,

    -- E[recovery cost] / P[recovery succeeds] for this failure type.
    -- Interpretation: expected µJ investment to obtain one successful recovery.
    -- NULL when recovery_success_rate = 0 (ETL guards division by zero).
    -- This feeds A4 EAR engine calibration directly.
    cost_per_recovery_success   REAL,

    -- Wall clock time this profile row was computed.
    computed_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- One profile per (type × group); ETL uses INSERT OR REPLACE.
    UNIQUE(failure_type_id, experiment_group)
);

-- Look up all profiles for a given failure type.
CREATE INDEX IF NOT EXISTS idx_fcp_type  ON failure_cost_profile(failure_type_id);

-- Look up all profiles for a given experiment group.
CREATE INDEX IF NOT EXISTS idx_fcp_group ON failure_cost_profile(experiment_group);


-- ---------------------------------------------------------------------------
-- View: v_failure_cost_comparison
--
-- Ranks failure types by cost_per_recovery_success within each experiment
-- group (descending — rank 1 is most expensive to recover from).
-- Primary query surface for Paper 8 cost-ranking tables.
-- Energy converted µJ → J for human-readable output.
-- ---------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_failure_cost_comparison AS
SELECT
    fcp.failure_type_id,
    ft.domain,
    ft.description,
    fcp.experiment_group,
    fcp.sample_count,
    fcp.recovery_cost_uj_mean        / 1e6 AS recovery_cost_j_mean,
    fcp.recovery_success_rate,
    fcp.cost_per_recovery_success    / 1e6 AS cost_per_success_j,
    -- Dense rank within group: 1 = most expensive recovery type.
    DENSE_RANK() OVER (
        PARTITION BY fcp.experiment_group
        ORDER BY fcp.cost_per_recovery_success DESC
    ) AS cost_rank
FROM failure_cost_profile fcp
JOIN failure_taxonomy ft ON fcp.failure_type_id = ft.failure_type_id
ORDER BY fcp.experiment_group, cost_rank;


-- ---------------------------------------------------------------------------
-- View: v_failure_cost_by_experiment
--
-- Same failure type across different experiment groups (models / providers).
-- Useful for comparing whether EAR benefit varies by LLM configuration.
-- ---------------------------------------------------------------------------

CREATE VIEW IF NOT EXISTS v_failure_cost_by_experiment AS
SELECT
    fcp.failure_type_id,
    ft.domain,
    fcp.experiment_group,
    e.model_name,
    e.provider,
    fcp.sample_count,
    fcp.recovery_cost_uj_mean        / 1e6 AS cost_j_mean,
    fcp.recovery_success_rate,
    fcp.cost_per_recovery_success    / 1e6 AS cost_per_success_j
FROM failure_cost_profile fcp
JOIN failure_taxonomy ft  ON fcp.failure_type_id  = ft.failure_type_id
-- Join via runs to get model_name and provider for this experiment group.
-- experiments.group_id = fcp.experiment_group is the correlation key.
JOIN experiments e        ON fcp.experiment_group = e.group_id
ORDER BY fcp.failure_type_id, e.provider;
