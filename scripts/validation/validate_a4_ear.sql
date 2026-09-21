-- validate_a4_ear.sql
-- Validation queries for A4 (EAR Policy Engine).
-- Run after calibration and at least one EAR-enabled experiment.
-- Rule PDS-4: all queries verified against GN100 schema.

-- ---------------------------------------------------------------------------
-- V1: ear_policy exists after calibration
-- EXPECTED: one row with policy_name = 'ear_v1' (or your policy name)
-- ---------------------------------------------------------------------------
SELECT 'V1' AS check_id, 'ear_policy_exists' AS check_name,
       ear_policy_id, policy_name, calibration_scope, created_at
FROM ear_policy;

-- ---------------------------------------------------------------------------
-- V2: ear_policy_rules populated for all expected failure types
-- EXPECTED: one row per failure type in failure_cost_profile for this group
-- Replace 'ear_v1' with your policy name.
-- ---------------------------------------------------------------------------
SELECT 'V2' AS check_id, 'rules_populated' AS check_name,
       epr.failure_type_id,
       epr.action,
       epr.max_attempts,
       ROUND(epr.cost_threshold_uj / 1e6, 4)      AS cost_threshold_j,
       ROUND(epr.calibrated_success_prob, 4)       AS calibrated_success_prob,
       epr.success_probability_threshold
FROM ear_policy_rules epr
JOIN ear_policy ep ON epr.ear_policy_id = ep.ear_policy_id
WHERE ep.policy_name = 'ear_v1'
ORDER BY epr.failure_type_id;

-- ---------------------------------------------------------------------------
-- V3: No rule has max_attempts = 0 with action = 'retry'
-- EXPECTED: 0 rows — would mean retry allowed but no attempts permitted
-- ---------------------------------------------------------------------------
SELECT 'V3' AS check_id, 'no_zero_attempt_retry' AS check_name,
       failure_type_id, max_attempts, action
FROM ear_policy_rules
WHERE max_attempts = 0 AND action = 'retry';

-- ---------------------------------------------------------------------------
-- V4: All failure_type_ids in ear_policy_rules exist in failure_taxonomy
-- EXPECTED: 0 rows
-- ---------------------------------------------------------------------------
SELECT 'V4' AS check_id, 'orphaned_failure_type' AS check_name,
       epr.failure_type_id
FROM ear_policy_rules epr
LEFT JOIN failure_taxonomy ft ON epr.failure_type_id = ft.failure_type_id
WHERE ft.failure_type_id IS NULL;

-- ---------------------------------------------------------------------------
-- V5: ear_decision_log populated after EAR experiment
-- EXPECTED: rows with action in (retry, abort, fallback)
-- Run after at least one experiment with retry_policy.engine: ear in YAML
-- ---------------------------------------------------------------------------
SELECT 'V5' AS check_id, 'decision_log_populated' AS check_name,
       action,
       reason,
       COUNT(*) AS decision_count,
       COUNT(DISTINCT run_id) AS runs
FROM ear_decision_log
GROUP BY action, reason
ORDER BY decision_count DESC;

-- ---------------------------------------------------------------------------
-- V6: EAR decisions are consistent with rules
-- abort decisions where action='retry' in rules = budget/prob gate fired
-- EXPECTED: informational — shows how often each gate triggered
-- ---------------------------------------------------------------------------
SELECT 'V6' AS check_id, 'decision_gate_distribution' AS check_name,
       edl.failure_type_id,
       edl.action,
       edl.reason,
       COUNT(*)                                        AS count,
       ROUND(AVG(edl.budget_remaining_uj) / 1e6, 4)  AS avg_budget_remaining_j,
       ROUND(AVG(edl.calibration_success_prob), 4)    AS avg_calib_success_prob
FROM ear_decision_log edl
GROUP BY edl.failure_type_id, edl.action, edl.reason
ORDER BY edl.failure_type_id, count DESC;

-- ---------------------------------------------------------------------------
-- V7: Calibration consistency — calibrated_success_prob matches A3 profile
-- EXPECTED: 0 rows (diff > 0.01 would indicate calibrator read stale data)
-- ---------------------------------------------------------------------------
SELECT 'V7' AS check_id, 'calibration_matches_profile' AS check_name,
       epr.failure_type_id,
       ROUND(epr.calibrated_success_prob, 4)       AS rule_success_prob,
       ROUND(fcp.recovery_success_rate, 4)         AS profile_success_rate,
       ABS(epr.calibrated_success_prob
           - fcp.recovery_success_rate)            AS diff
FROM ear_policy_rules epr
JOIN ear_policy ep  ON epr.ear_policy_id = ep.ear_policy_id
JOIN failure_cost_profile fcp
     ON epr.failure_type_id = fcp.failure_type_id
WHERE ep.policy_name = 'ear_v1'
  AND fcp.recovery_success_rate IS NOT NULL
  AND ABS(epr.calibrated_success_prob - fcp.recovery_success_rate) > 0.01;
