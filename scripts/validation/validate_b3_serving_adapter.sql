-- =============================================================================
-- VALIDATION: 8.6-B3 Serving Engine Adapter Interface
-- =============================================================================
-- Run after B3 is wired into the runner and an experiment completes.
-- All checks against the existing B2 tables — B3 adds no schema.
-- Expected: same row counts as before B3 on current remote API platforms.
-- RemoteAPIAdapter produces empty metrics just like NoOpCollector.
--
-- Usage:
--   sqlite3 $DB < validate_b3_serving_adapter.sql
-- =============================================================================

-- CHECK 1: state_reuse_taxonomy still intact (B2 tables untouched by B3)
SELECT 'CHECK 1: state_reuse_taxonomy rows' AS check_name,
       COUNT(*) AS result,
       CASE WHEN COUNT(*) = 6 THEN 'PASS' ELSE 'FAIL — expected 6 rows' END AS status
FROM state_reuse_taxonomy;

-- CHECK 2: state_reuse_events — empty is correct on remote API (B2.4 / B2.5)
SELECT 'CHECK 2: state_reuse_events empty on remote API' AS check_name,
       COUNT(*) AS result,
       CASE WHEN COUNT(*) = 0 THEN 'PASS (remote API — expected empty)'
            ELSE 'INFO — rows present (vLLM correlation active)' END AS status
FROM state_reuse_events;

-- CHECK 3: cache_state_snapshots — empty is correct on remote API
SELECT 'CHECK 3: cache_state_snapshots empty on remote API' AS check_name,
       COUNT(*) AS result,
       CASE WHEN COUNT(*) = 0 THEN 'PASS (remote API — expected empty)'
            ELSE 'INFO — rows present (vLLM telemetry active)' END AS status
FROM cache_state_snapshots;

-- CHECK 4: v_state_reuse_impact view still runs (B2 view, not broken by B3)
SELECT 'CHECK 4: v_state_reuse_impact view functional' AS check_name,
       COUNT(*) AS result,
       'PASS' AS status
FROM v_state_reuse_impact;

-- CHECK 5: No orphan state_reuse_events (recovery_id FK integrity)
SELECT 'CHECK 5: state_reuse_events FK integrity' AS check_name,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — orphan rows without matching recovery_events' END AS status
FROM state_reuse_events sre
LEFT JOIN recovery_events re ON sre.recovery_id = re.recovery_id
WHERE sre.recovery_id IS NOT NULL
  AND re.recovery_id IS NULL;

-- CHECK 6: No orphan cache_state_snapshots (attempt_id FK integrity)
SELECT 'CHECK 6: cache_state_snapshots FK integrity' AS check_name,
       COUNT(*) AS orphan_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — orphan rows without matching goal_attempt' END AS status
FROM cache_state_snapshots css
LEFT JOIN goal_attempt ga ON css.attempt_id = ga.attempt_id
WHERE css.attempt_id IS NOT NULL
  AND ga.attempt_id IS NULL;

-- CHECK 7: reuse_fraction bounds (B3.8 / A3.8 structural invariants)
SELECT 'CHECK 7: reuse_fraction in [0,1]' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — reuse_fraction out of bounds' END AS status
FROM state_reuse_events
WHERE reuse_fraction IS NOT NULL
  AND (reuse_fraction < 0 OR reuse_fraction > 1);

-- CHECK 8: occupancy_fraction bounds
SELECT 'CHECK 8: occupancy_fraction in [0,1]' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — occupancy_fraction out of bounds' END AS status
FROM cache_state_snapshots
WHERE occupancy_fraction IS NOT NULL
  AND (occupancy_fraction < 0 OR occupancy_fraction > 1);

-- CHECK 9: cache_hit valid values (1, 0, or NULL only)
SELECT 'CHECK 9: cache_hit valid values' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — cache_hit has values other than 0, 1, NULL' END AS status
FROM state_reuse_events
WHERE cache_hit IS NOT NULL
  AND cache_hit NOT IN (0, 1);

-- CHECK 10: reuse_type FK validity (must match taxonomy)
SELECT 'CHECK 10: reuse_type FK valid' AS check_name,
       COUNT(*) AS violation_count,
       CASE WHEN COUNT(*) = 0 THEN 'PASS'
            ELSE 'FAIL — reuse_type not in state_reuse_taxonomy' END AS status
FROM state_reuse_events sre
LEFT JOIN state_reuse_taxonomy srt ON sre.reuse_type = srt.reuse_type_id
WHERE srt.reuse_type_id IS NULL;

-- Summary for vLLM (informational — only meaningful when vLLM is active)
SELECT 'SUMMARY: engine_backed collector stats' AS check_name,
       'remote_api: both tables empty (correct) | '
       'vllm_stub: both tables empty (correct) | '
       'vllm_full: snapshots populated, events populated if request_level_correlation=true'
       AS expected_state;
