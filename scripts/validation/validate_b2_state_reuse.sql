-- scripts/validation/validate_b2_state_reuse.sql
--
-- Validation checks for 8.6-B2: State Reuse and Cache Telemetry
-- Run: sqlite3 $DB < scripts/validation/validate_b2_state_reuse.sql
--
-- Expected: all checks return 0 violations or expected row counts.
-- Empty state_reuse_events and cache_state_snapshots is CORRECT on
-- platforms without serving engine telemetry (B2.5).

-- CHECK 1: state_reuse_taxonomy table exists with 6 seed rows
SELECT
    'CHECK 1: state_reuse_taxonomy exists with 6 rows' AS check_name,
    COUNT(*) AS row_count,
    CASE WHEN COUNT(*) = 6 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_taxonomy;

-- CHECK 2: all three layers represented in taxonomy
SELECT
    'CHECK 2: all three layers in taxonomy' AS check_name,
    COUNT(DISTINCT layer) AS layer_count,
    CASE WHEN COUNT(DISTINCT layer) = 3 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_taxonomy;

-- CHECK 3: state_reuse_events table exists (may be empty — B2.5)
SELECT
    'CHECK 3: state_reuse_events table exists' AS check_name,
    COUNT(*) AS row_count,
    '✓' AS status
FROM state_reuse_events;

-- CHECK 4: cache_state_snapshots table exists (may be empty — B2.5)
SELECT
    'CHECK 4: cache_state_snapshots table exists' AS check_name,
    COUNT(*) AS row_count,
    '✓' AS status
FROM cache_state_snapshots;

-- CHECK 5: v_state_reuse_impact view runs without error
SELECT
    'CHECK 5: v_state_reuse_impact view runs' AS check_name,
    COUNT(*) AS row_count,
    '✓' AS status
FROM v_state_reuse_impact;

-- CHECK 6: referential integrity — recovery_id FK
-- All state_reuse_events.recovery_id must resolve to recovery_events.
SELECT
    'CHECK 6: state_reuse_events recovery_id FK integrity' AS check_name,
    COUNT(*) AS orphan_count,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_events sre
WHERE sre.recovery_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM recovery_events re
      WHERE re.recovery_id = sre.recovery_id
  );

-- CHECK 7: referential integrity — attempt_id FK in state_reuse_events
SELECT
    'CHECK 7: state_reuse_events attempt_id FK integrity' AS check_name,
    COUNT(*) AS orphan_count,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_events sre
WHERE sre.attempt_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM goal_attempt ga
      WHERE ga.attempt_id = sre.attempt_id
  );

-- CHECK 8: referential integrity — attempt_id FK in cache_state_snapshots
SELECT
    'CHECK 8: cache_state_snapshots attempt_id FK integrity' AS check_name,
    COUNT(*) AS orphan_count,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM cache_state_snapshots css
WHERE css.attempt_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM goal_attempt ga
      WHERE ga.attempt_id = css.attempt_id
  );

-- CHECK 9: reuse_fraction bounds (0 to 1)
SELECT
    'CHECK 9: reuse_fraction bounds' AS check_name,
    COUNT(*) AS out_of_bounds,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_events
WHERE reuse_fraction IS NOT NULL
  AND (reuse_fraction < 0 OR reuse_fraction > 1);

-- CHECK 10: occupancy_fraction bounds in snapshots
SELECT
    'CHECK 10: occupancy_fraction bounds' AS check_name,
    COUNT(*) AS out_of_bounds,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM cache_state_snapshots
WHERE occupancy_fraction IS NOT NULL
  AND (occupancy_fraction < 0 OR occupancy_fraction > 1);

-- CHECK 11: reuse_type values all known in taxonomy
SELECT
    'CHECK 11: reuse_type FK valid' AS check_name,
    COUNT(*) AS unknown_types,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_events sre
WHERE NOT EXISTS (
    SELECT 1 FROM state_reuse_taxonomy srt
    WHERE srt.reuse_type_id = sre.reuse_type
);

-- CHECK 12: cache_hit values are only 0, 1, or NULL
SELECT
    'CHECK 12: cache_hit valid values' AS check_name,
    COUNT(*) AS invalid_values,
    CASE WHEN COUNT(*) = 0 THEN '✓' ELSE 'FAIL' END AS status
FROM state_reuse_events
WHERE cache_hit IS NOT NULL
  AND cache_hit NOT IN (0, 1);

-- CHECK 13: indexes exist
SELECT
    'CHECK 13: indexes exist' AS check_name,
    COUNT(*) AS index_count,
    CASE WHEN COUNT(*) = 6 THEN '✓' ELSE 'FAIL' END AS status
FROM sqlite_master
WHERE type = 'index'
  AND name IN (
      'idx_sre_recovery',
      'idx_sre_attempt',
      'idx_sre_run',
      'idx_sre_type',
      'idx_css_run',
      'idx_css_attempt'
  );
