-- =============================================================================
-- validate_a1_taxonomy.sql
-- SPEC 8.6-A1: Post-migration validation queries.
-- Run after v098 + s014 + v099 are applied and after backfill.
-- All queries must return EXPECTED result — any deviation is a data error.
-- Usage:
--   DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools');
--        from path_loader import get_alems_db_path; print(get_alems_db_path())")
--   sqlite3 "$DB" < scripts/validation/validate_a1_taxonomy.sql
-- =============================================================================

-- V1: All existing tool_failure_events.failure_type values exist in taxonomy.
-- EXPECTED: 0 rows (no orphaned failure_type values after reconstruction).
SELECT 'V1' AS check_id,
       'orphaned failure_type in tool_failure_events' AS check_name,
       COUNT(*) AS violations
FROM (
    SELECT DISTINCT tfe.failure_type
    FROM tool_failure_events tfe
    LEFT JOIN failure_taxonomy ft ON tfe.failure_type = ft.failure_type_id
    WHERE ft.failure_type_id IS NULL
);
-- EXPECTED violations: 0

-- V2: No data loss in goal_attempt after reconstruction.
-- EXPECTED: row count matches pre-migration count (1814 on GN100 as of spec).
SELECT 'V2' AS check_id,
       'goal_attempt row count' AS check_name,
       COUNT(*) AS row_count
FROM goal_attempt;
-- EXPECTED row_count: >= 1814 (never less; new runs may have been added)

-- V3: No data loss in tool_failure_events after reconstruction.
-- EXPECTED: row count matches pre-migration count (287 on GN100 as of spec).
SELECT 'V3' AS check_id,
       'tool_failure_events row count' AS check_name,
       COUNT(*) AS row_count
FROM tool_failure_events;
-- EXPECTED row_count: >= 287

-- V4: winning_attempt_id is set for all goals where success=1 and a
-- winning attempt exists in goal_attempt.
-- EXPECTED: 0 rows (no successful goals missing their winning_attempt_id).
SELECT 'V4' AS check_id,
       'successful goals missing winning_attempt_id' AS check_name,
       COUNT(*) AS violations
FROM goal_execution ge
WHERE ge.success = 1
  AND ge.winning_attempt_id IS NULL
  AND EXISTS (
      SELECT 1 FROM goal_attempt ga
      WHERE ga.goal_id = ge.goal_id
        AND ga.is_winning = 1
  );
-- EXPECTED violations: 0

-- V5: Taxonomy tables have expected canonical row counts.
-- EXPECTED: failure_taxonomy = 13, recovery_taxonomy = 9.
SELECT 'V5a' AS check_id,
       'failure_taxonomy row count' AS check_name,
       COUNT(*) AS row_count
FROM failure_taxonomy;
-- EXPECTED row_count: 14 (13 canonical + crashed added by s015)

SELECT 'V5b' AS check_id,
       'recovery_taxonomy row count' AS check_name,
       COUNT(*) AS row_count
FROM recovery_taxonomy;
-- EXPECTED row_count: 9

-- V6: All goal_attempt.failure_type values that are non-NULL exist in taxonomy.
-- EXPECTED: 0 rows (FK integrity check).
SELECT 'V6' AS check_id,
       'orphaned failure_type in goal_attempt' AS check_name,
       COUNT(*) AS violations
FROM (
    SELECT DISTINCT ga.failure_type
    FROM goal_attempt ga
    LEFT JOIN failure_taxonomy ft ON ga.failure_type = ft.failure_type_id
    WHERE ga.failure_type IS NOT NULL
      AND ft.failure_type_id IS NULL
);
-- EXPECTED violations: 0

-- V7: goal_attempt.outcome values contain only the new 3-value set.
-- EXPECTED: 0 rows (no legacy outcome values remain).
SELECT 'V7' AS check_id,
       'legacy outcome values in goal_attempt' AS check_name,
       COUNT(*) AS violations
FROM goal_attempt
WHERE outcome NOT IN ('success', 'failure', 'partial');
-- EXPECTED violations: 0

-- V8: failure_cause column is retained by design (SC-5, design decision 8.6-A1).
-- failure_cause = coarse runtime classification (written by goal_tracker.py).
-- failure_type  = fine-grained taxonomy FK (written by recorder + backfill).
-- This check confirms failure_cause IS present — removing it would be a regression.
-- EXPECTED: 1 row.
SELECT 'V8' AS check_id,
       'failure_cause column present in goal_attempt (expected)' AS check_name,
       COUNT(*) AS present
FROM pragma_table_info('goal_attempt')
WHERE name = 'failure_cause';
-- EXPECTED present: 1

-- V9: winning_attempt_id column must exist in goal_execution.
-- EXPECTED: 1 row.
SELECT 'V9' AS check_id,
       'winning_attempt_id column exists in goal_execution' AS check_name,
       COUNT(*) AS present
FROM pragma_table_info('goal_execution')
WHERE name = 'winning_attempt_id';
-- EXPECTED present: 1
