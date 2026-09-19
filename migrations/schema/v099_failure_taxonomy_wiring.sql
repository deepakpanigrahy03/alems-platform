-- =============================================================================
-- v099_failure_taxonomy_wiring.sql
-- SPEC 8.6-A1 (A1.7 + backfill): Safe additive-only migration.
-- NO table reconstruction. NO column drops. Safe on any machine regardless
-- of migration history (v080 through v097 all have the columns referenced).
--
-- Three operations:
--   1. ADD winning_attempt_id to goal_execution (additive ALTER).
--   2. Backfill goal_attempt.failure_type from failure_cause where missing.
--      failure_cause is kept as historical coarse classification — not dropped.
--   3. Backfill winning_attempt_id from goal_attempt.is_winning.
--
-- Taxonomy FK enforcement is handled at application layer in
-- tool_failure_recorder.py — not at DB layer. This is intentional:
-- application-layer enforcement allows taxonomy to grow via INSERT into
-- failure_taxonomy with zero migration and zero DB constraint changes.
--
-- MSC-4: DDL (ALTER TABLE) and DML (UPDATE) may coexist in a single
-- migration when the DML is a data-preserving backfill of a column
-- added in the same migration. Precedent: v099 follows v093 pattern.
-- Prerequisite: v098 (failure_taxonomy, recovery_taxonomy must exist).
-- =============================================================================

-- ── Operation 1: Add winning_attempt_id to goal_execution ────────────────────
-- Additive ALTER — safe on all machines. NULL for all existing rows at add
-- time; backfill in Operation 3 below sets values where data exists.
-- winning_run_id is retained for backward compat — not deprecated, not dropped.
-- winning_attempt_id provides a direct FK path goal → attempt without
-- joining through runs, which winning_run_id requires.
ALTER TABLE goal_execution
    ADD COLUMN winning_attempt_id INTEGER
    REFERENCES goal_attempt(attempt_id);

CREATE INDEX IF NOT EXISTS idx_goal_exec_winning_attempt
    ON goal_execution(winning_attempt_id);

-- ── Operation 2: Backfill failure_type from failure_cause ────────────────────
-- failure_cause (coarse: timeout/api_error/tool_error/wrong_answer/
-- context_overflow/rate_limit) was the original classification column.
-- failure_type (fine-grained FK to taxonomy) was added later and is
-- not consistently populated for older rows.
-- This UPDATE rescues the coarse value into failure_type where fine-grained
-- value is missing. failure_cause column is kept — it has research value
-- as the original runtime classification and is written by goal_tracker.py.
-- Only rows where failure_type IS NULL get updated — existing fine-grained
-- values are never overwritten.
UPDATE goal_attempt
SET failure_type = failure_cause
WHERE failure_type IS NULL
  AND failure_cause IS NOT NULL
  AND failure_cause IN (
      -- Only copy values that are valid taxonomy entries.
      -- context_overflow maps to capability_error (closest semantic match).
      -- wrong_answer maps to semantic_error.
      -- Others are direct matches to taxonomy failure_type_id values.
      'timeout', 'api_error', 'tool_error', 'rate_limit', 'auth_error'
  );

-- Handle the two non-direct mappings separately.
UPDATE goal_attempt
SET failure_type = 'capability_error'
WHERE failure_type IS NULL
  AND failure_cause = 'context_overflow';

UPDATE goal_attempt
SET failure_type = 'semantic_error'
WHERE failure_type IS NULL
  AND failure_cause = 'wrong_answer';

-- ── Operation 3: Backfill winning_attempt_id ─────────────────────────────────
-- Set winning_attempt_id for all goals that have a winning attempt.
-- Uses is_winning=1 flag on goal_attempt as the authoritative source.
-- Goals with success=0 and no winning attempt keep winning_attempt_id=NULL.
UPDATE goal_execution
SET winning_attempt_id = (
    SELECT ga.attempt_id
    FROM goal_attempt ga
    WHERE ga.goal_id = goal_execution.goal_id
      AND ga.is_winning = 1
    LIMIT 1
)
WHERE EXISTS (
    SELECT 1 FROM goal_attempt ga
    WHERE ga.goal_id = goal_execution.goal_id
      AND ga.is_winning = 1
);
