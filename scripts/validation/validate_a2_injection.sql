-- =============================================================================
-- validate_a2_injection.sql
-- SPEC 8.6-A2: Post-run validation queries for failure_injection_log.
-- Run after a scenario-mode injection experiment completes.
-- Replace 'paper8_hallucination_v1' with your actual scenario_id.
-- Usage:
--   DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools');
--        from path_loader import get_alems_db_path; print(get_alems_db_path())")
--   sqlite3 "$DB" < scripts/validation/validate_a2_injection.sql
-- =============================================================================

-- V1: failure_injection_log table exists and has expected columns.
-- EXPECTED: 15 rows (one per column).
SELECT 'V1' AS check_id,
       'failure_injection_log columns' AS check_name,
       COUNT(*) AS column_count
FROM pragma_table_info('failure_injection_log');
-- EXPECTED column_count: 15

-- V2: Every injection log row references a valid failure_taxonomy entry
-- (or injected_type IS NULL for non-injected decisions).
-- EXPECTED: 0 rows (no orphaned injected_type values).
SELECT 'V2' AS check_id,
       'orphaned injected_type in failure_injection_log' AS check_name,
       COUNT(*) AS violations
FROM failure_injection_log fil
LEFT JOIN failure_taxonomy ft ON fil.injected_type = ft.failure_type_id
WHERE fil.injected_type IS NOT NULL
  AND ft.failure_type_id IS NULL;
-- EXPECTED violations: 0

-- V3: Status distribution for most recent scenario run.
-- Replace scenario_id value with your actual scenario_id.
-- EXPECTED: 'injected' count > 0 after a real injection run.
SELECT 'V3' AS check_id,
       fil.status,
       COUNT(*) AS event_count
FROM failure_injection_log fil
WHERE fil.scenario_id = (
    SELECT scenario_id FROM failure_injection_log
    ORDER BY created_at DESC LIMIT 1
)
GROUP BY fil.status
ORDER BY event_count DESC;
-- EXPECTED: at least one row with status='injected'.

-- V4: Seed determinism check — same scenario_id + rule_index + draw_number
-- must produce the same random_seed across runs.
-- EXPECTED: 0 rows (no duplicate scenario/rule/draw with different seeds).
SELECT 'V4' AS check_id,
       'non-deterministic seeds' AS check_name,
       COUNT(*) AS violations
FROM (
    SELECT scenario_id, rule_index, draw_number,
           COUNT(DISTINCT random_seed) AS seed_variants
    FROM failure_injection_log
    WHERE random_seed != ''
    GROUP BY scenario_id, rule_index, draw_number
    HAVING seed_variants > 1
);
-- EXPECTED violations: 0

-- V5: All injection_log rows have attempt_id set (never NULL after flush).
-- EXPECTED: 0 rows.
SELECT 'V5' AS check_id,
       'injection_log rows missing attempt_id' AS check_name,
       COUNT(*) AS violations
FROM failure_injection_log
WHERE attempt_id IS NULL;
-- EXPECTED violations: 0

-- V6: Dry-run check — if any run used dry_run=true,
-- status='injected' must never appear for those rows.
-- (dry_run rows use status='selected' instead.)
-- This query is informational — shows selected vs injected counts.
SELECT 'V6' AS check_id,
       status,
       COUNT(*) AS count
FROM failure_injection_log
WHERE status IN ('injected', 'selected')
GROUP BY status;
-- INFORMATIONAL: 'injected' = real injections, 'selected' = dry-run decisions.
