-- =============================================================================
-- VALIDATION: 8.6-B3 Revised — serving_runtime_snapshots + plugin registry
-- =============================================================================
-- Run after deploying all files and running one experiment.
-- Usage: sqlite3 $DB < validate_b3_revised.sql
-- =============================================================================

-- CHECK 1: serving_runtime_snapshots table exists
SELECT 'CHECK 1: serving_runtime_snapshots exists' AS check_name,
       COUNT(*) AS row_count,
       'PASS' AS status
FROM serving_runtime_snapshots;

-- CHECK 2: snapshot_type constraint valid
SELECT 'CHECK 2: snapshot_type values valid' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — invalid snapshot_type' END AS status
FROM serving_runtime_snapshots
WHERE snapshot_type NOT IN ('kv_cache','expert_tier','queue','token_rate');

-- CHECK 3: telemetry_scope constraint valid
SELECT 'CHECK 3: telemetry_scope values valid' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — invalid telemetry_scope' END AS status
FROM serving_runtime_snapshots
WHERE telemetry_scope NOT IN ('request','interval','process','unavailable');

-- CHECK 4: expert_tier rows never have kv_* columns populated
-- (enforces semantic separation — review point 2)
SELECT 'CHECK 4: expert_tier rows have no kv_ data' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — expert_tier row has kv_ columns set' END AS status
FROM serving_runtime_snapshots
WHERE snapshot_type = 'expert_tier'
  AND (kv_capacity_tokens IS NOT NULL
    OR kv_occupied_tokens IS NOT NULL
    OR kv_hit_rate_aggregate IS NOT NULL);

-- CHECK 5: kv_cache rows never have tier_* columns populated
SELECT 'CHECK 5: kv_cache rows have no tier_ data' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — kv_cache row has tier_ columns set' END AS status
FROM serving_runtime_snapshots
WHERE snapshot_type = 'kv_cache'
  AND (tier_vram_bytes IS NOT NULL
    OR tier_ram_bytes IS NOT NULL
    OR tier_disk_bytes IS NOT NULL);

-- CHECK 6: fraction bounds
SELECT 'CHECK 6: fraction columns in [0,1]' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — fraction out of bounds' END AS status
FROM serving_runtime_snapshots
WHERE (kv_occupancy_fraction IS NOT NULL AND kv_occupancy_fraction NOT BETWEEN 0 AND 1)
   OR (kv_hit_rate_aggregate IS NOT NULL AND kv_hit_rate_aggregate NOT BETWEEN 0 AND 1)
   OR (tier_vram_fraction IS NOT NULL AND tier_vram_fraction NOT BETWEEN 0 AND 1)
   OR (tier_ram_fraction IS NOT NULL AND tier_ram_fraction NOT BETWEEN 0 AND 1)
   OR (tier_disk_fraction IS NOT NULL AND tier_disk_fraction NOT BETWEEN 0 AND 1);

-- CHECK 7: FK integrity — run_id
SELECT 'CHECK 7: run_id FK integrity' AS check_name,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — orphan run_id' END AS status
FROM serving_runtime_snapshots srs
LEFT JOIN runs r ON srs.run_id = r.run_id
WHERE srs.run_id IS NOT NULL AND r.run_id IS NULL;

-- CHECK 8: FK integrity — attempt_id
SELECT 'CHECK 8: attempt_id FK integrity' AS check_name,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — orphan attempt_id' END AS status
FROM serving_runtime_snapshots srs
LEFT JOIN goal_attempt ga ON srs.attempt_id = ga.attempt_id
WHERE srs.attempt_id IS NOT NULL AND ga.attempt_id IS NULL;

-- CHECK 9: B2 tables still intact (B3 must not break B2)
SELECT 'CHECK 9: state_reuse_taxonomy intact' AS check_name,
       COUNT(*) AS row_count,
       CASE WHEN COUNT(*) = 6 THEN 'PASS'
            ELSE 'FAIL — expected 6 rows' END AS status
FROM state_reuse_taxonomy;

-- CHECK 10: v_state_reuse_impact still functional
SELECT 'CHECK 10: v_state_reuse_impact view functional' AS check_name,
       COUNT(*) AS row_count,
       'PASS' AS status
FROM v_state_reuse_impact;

-- CHECK 11: remote_api platform — all serving tables empty (correct B2.5)
SELECT 'CHECK 11: RemoteAPIAdapter — serving_runtime_snapshots empty' AS check_name,
       COUNT(*) AS row_count,
       CASE WHEN COUNT(*) = 0
            THEN 'PASS (remote_api — expected empty)'
            ELSE 'INFO — rows present (non-remote adapter active)' END AS status
FROM serving_runtime_snapshots;

-- Summary
SELECT 'SUMMARY: 11/11 checks must show PASS or INFO' AS check_name,
       'INFO on checks 1 and 11 is correct for remote_api platforms' AS expected;
