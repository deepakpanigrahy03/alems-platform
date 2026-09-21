-- validate_a5_comparison.sql
-- Chunk 8.6-A5 regression checks
-- Run after applying v107 and s016 migrations.

-- V1: v107 view exists
SELECT 'V1_view_exists' AS check_name,
       COUNT(*) AS result,
       '1' AS expected
FROM sqlite_master
WHERE type = 'view'
  AND name = 'v_wasted_energy_per_success';

-- V2: policy_comparison_results table exists
SELECT 'V2_table_exists' AS check_name,
       COUNT(*) AS result,
       '1' AS expected
FROM sqlite_master
WHERE type = 'table'
  AND name = 'policy_comparison_results';

-- V3: all four reference policies seeded
SELECT 'V3_policy_count' AS check_name,
       COUNT(*) AS result,
       '4' AS expected
FROM ear_policy
WHERE policy_name IN (
    'flat_default', 'ear_conservative', 'ear_v1', 'ear_aggressive'
);

-- V4: each policy has 14 rules
SELECT 'V4_rules_per_policy' AS check_name,
       p.policy_name,
       COUNT(*) AS rule_count,
       '14' AS expected
FROM ear_policy_rules r
JOIN ear_policy p ON p.ear_policy_id = r.ear_policy_id
WHERE p.policy_name IN (
    'flat_default', 'ear_conservative', 'ear_v1', 'ear_aggressive'
)
GROUP BY p.policy_name
ORDER BY p.policy_name;

-- V5: flat_default has NULL thresholds
SELECT 'V5_flat_null_thresholds' AS check_name,
       COUNT(*) AS result,
       '14' AS expected
FROM ear_policy_rules r
JOIN ear_policy p ON p.ear_policy_id = r.ear_policy_id
WHERE p.policy_name = 'flat_default'
  AND r.cost_threshold_uj IS NULL
  AND r.success_probability_threshold IS NULL;

-- V6: EAR policies have non-NULL thresholds
SELECT 'V6_ear_non_null_thresholds' AS check_name,
       p.policy_name,
       COUNT(*) AS non_null_count
FROM ear_policy_rules r
JOIN ear_policy p ON p.ear_policy_id = r.ear_policy_id
WHERE p.policy_name IN ('ear_conservative', 'ear_v1', 'ear_aggressive')
  AND r.cost_threshold_uj IS NOT NULL
  AND r.success_probability_threshold IS NOT NULL
GROUP BY p.policy_name
ORDER BY p.policy_name;

-- V7: view returns data when experiments exist
SELECT 'V7_view_query' AS check_name,
       COUNT(*) AS group_count
FROM v_wasted_energy_per_success;

-- V8: policy_comparison_results index exists
SELECT 'V8_index_exists' AS check_name,
       COUNT(*) AS result,
       '1' AS expected
FROM sqlite_master
WHERE type = 'index'
  AND name = 'idx_pcr_groups';

-- V9: ear_aggressive has higher cost thresholds than ear_conservative
SELECT 'V9_aggressive_gt_conservative' AS check_name,
       a.failure_type_id,
       a.cost_threshold_uj AS aggressive_uj,
       c.cost_threshold_uj AS conservative_uj,
       CASE WHEN a.cost_threshold_uj > c.cost_threshold_uj
            THEN 'PASS' ELSE 'FAIL' END AS result
FROM ear_policy_rules a
JOIN ear_policy pa ON pa.ear_policy_id = a.ear_policy_id AND pa.policy_name = 'ear_aggressive'
JOIN ear_policy_rules c ON c.failure_type_id = a.failure_type_id
JOIN ear_policy pc ON pc.ear_policy_id = c.ear_policy_id AND pc.policy_name = 'ear_conservative'
ORDER BY a.failure_type_id;
