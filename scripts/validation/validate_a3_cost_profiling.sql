-- validate_a3_cost_profiling.sql
-- Validation queries for A3 (Per-Failure-Type Cost Profiling).
-- Run after compute_failure_cost_profiles() completes for a test group.
-- All queries must return 0 rows or match EXPECTED comments.
-- Rule PDS-4: every query here has been verified against GN100 schema.

-- ---------------------------------------------------------------------------
-- V1: Fan-out prevention check
-- EXPECTED: 0 rows
-- A non-zero result means tool_failure_events.attempt_id is not one-to-one
-- with goal_attempt. Cost profiles would be inflated. Abort ETL and investigate.
-- ---------------------------------------------------------------------------
SELECT 'V1' AS check_id, 'fan_out_prevention' AS check_name,
       tfe.failure_id, COUNT(*) AS join_count
FROM tool_failure_events tfe
JOIN goal_attempt ga ON tfe.attempt_id = ga.attempt_id
GROUP BY tfe.failure_id
HAVING join_count > 1;

-- ---------------------------------------------------------------------------
-- V2: Profiles exist after ETL run for the test group
-- EXPECTED: one row per failure type with sample_count > 0
-- Replace 'YOUR_GROUP_ID' with the actual experiments.group_id used.
-- ---------------------------------------------------------------------------
SELECT 'V2' AS check_id, 'profiles_exist' AS check_name,
       failure_type_id,
       sample_count,
       ROUND(recovery_cost_uj_mean / 1e6, 4)       AS cost_j_mean,
       ROUND(recovery_success_rate, 4)              AS success_rate,
       ROUND(cost_per_recovery_success / 1e6, 4)   AS cost_per_success_j
FROM failure_cost_profile
WHERE experiment_group = 'YOUR_GROUP_ID'
ORDER BY cost_per_recovery_success DESC;

-- ---------------------------------------------------------------------------
-- V3: cost_per_recovery_success internal consistency
-- EXPECTED: 0 rows (stored value matches recomputed value within 1 µJ)
-- Catches floating-point drift or ETL logic errors.
-- ---------------------------------------------------------------------------
SELECT 'V3' AS check_id, 'cost_per_success_consistent' AS check_name,
       failure_type_id,
       experiment_group,
       ROUND(recovery_cost_uj_mean / NULLIF(recovery_success_rate, 0), 4) AS computed,
       ROUND(cost_per_recovery_success, 4)                                 AS stored,
       ABS(
           recovery_cost_uj_mean / NULLIF(recovery_success_rate, 0)
           - cost_per_recovery_success
       )                                                                    AS diff_uj
FROM failure_cost_profile
WHERE cost_per_recovery_success IS NOT NULL
  AND ABS(
      recovery_cost_uj_mean / NULLIF(recovery_success_rate, 0)
      - cost_per_recovery_success
  ) > 1.0;

-- ---------------------------------------------------------------------------
-- V4: No profile row has sample_count = 0 with non-NULL cost stats
-- EXPECTED: 0 rows
-- A sample_count=0 row must have all cost columns NULL (nothing to aggregate).
-- ---------------------------------------------------------------------------
SELECT 'V4' AS check_id, 'zero_sample_no_cost_stats' AS check_name,
       failure_type_id, experiment_group, sample_count,
       recovery_cost_uj_mean
FROM failure_cost_profile
WHERE sample_count = 0
  AND recovery_cost_uj_mean IS NOT NULL;

-- ---------------------------------------------------------------------------
-- V5: success_count never exceeds sample_count
-- EXPECTED: 0 rows
-- ---------------------------------------------------------------------------
SELECT 'V5' AS check_id, 'success_count_lte_sample_count' AS check_name,
       failure_type_id, experiment_group,
       recovery_success_count, sample_count
FROM failure_cost_profile
WHERE recovery_success_count > sample_count;

-- ---------------------------------------------------------------------------
-- V6: All failure_type_ids in failure_cost_profile exist in failure_taxonomy
-- EXPECTED: 0 rows
-- Application-layer FK enforcement check (no DB-level FK on this extension table).
-- ---------------------------------------------------------------------------
SELECT 'V6' AS check_id, 'orphaned_failure_type' AS check_name,
       fcp.failure_type_id,
       fcp.experiment_group
FROM failure_cost_profile fcp
LEFT JOIN failure_taxonomy ft ON fcp.failure_type_id = ft.failure_type_id
WHERE ft.failure_type_id IS NULL;

-- ---------------------------------------------------------------------------
-- V7: View v_failure_cost_comparison returns rows for the test group
-- EXPECTED: one ranked row per failure type
-- ---------------------------------------------------------------------------
SELECT 'V7' AS check_id, 'view_comparison_populated' AS check_name,
       failure_type_id, domain, experiment_group,
       ROUND(recovery_cost_j_mean, 4)  AS cost_j_mean,
       ROUND(recovery_success_rate, 4) AS success_rate,
       cost_rank
FROM v_failure_cost_comparison
WHERE experiment_group = 'YOUR_GROUP_ID'
ORDER BY cost_rank;

-- ---------------------------------------------------------------------------
-- V8: Summary — profile count and coverage per group
-- Informational; not a pass/fail check.
-- ---------------------------------------------------------------------------
SELECT 'V8' AS check_id, 'summary' AS check_name,
       experiment_group,
       COUNT(*)                                           AS profile_count,
       SUM(sample_count)                                  AS total_failure_events,
       ROUND(AVG(recovery_success_rate), 3)               AS avg_success_rate,
       ROUND(MIN(cost_per_recovery_success) / 1e6, 4)    AS min_cost_per_success_j,
       ROUND(MAX(cost_per_recovery_success) / 1e6, 4)    AS max_cost_per_success_j,
       MAX(computed_at)                                   AS last_computed
FROM failure_cost_profile
GROUP BY experiment_group
ORDER BY experiment_group;
