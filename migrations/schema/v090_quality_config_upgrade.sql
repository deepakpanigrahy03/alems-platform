-- =============================================================================
-- v090_quality_config_upgrade.sql
-- Core migration: Upgrade quality scoring schema for N-judge support
-- =============================================================================
--
-- Changes:
--   1. ADD n_judges column to task_quality_config (replaces dual_judge semantics)
--   2. ADD judge_model_set column to task_quality_config (JSON array of models)
--   3. ADD rubric column to task_quality_config (JSON rubric for scalar tasks)
--   4. ADD success_threshold column to task_quality_config (replaces threshold)
--   5. ADD task_category column to output_quality
--   6. Recreate output_quality with expanded score_method CHECK constraint
--      (SQLite cannot ALTER CHECK — recreation with data preservation)
--
-- Rule SC-5: dual_judge and threshold columns are kept (never dropped).
-- Rule SC-7: schema_version entry added.
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

-- ── task_quality_config additions ────────────────────────────────────────────

-- n_judges: 1=single judge, 2-5=multi-judge with median reconciliation.
-- Replaces dual_judge (dual_judge=1 equivalent to n_judges=2).
-- "duplicate column name" handled by apply_one idempotency.
ALTER TABLE task_quality_config ADD COLUMN n_judges INTEGER NOT NULL DEFAULT 1;

-- judge_model_set: JSON array of judge model IDs.
-- NULL = use default judge model in quality_judge.py.
ALTER TABLE task_quality_config ADD COLUMN judge_model_set TEXT;

-- rubric: JSON rubric for scalar tasks.
-- NULL for binary/testsuite tasks.
ALTER TABLE task_quality_config ADD COLUMN rubric TEXT;

-- success_threshold: alias for threshold with clearer name.
-- threshold column kept for backward compat (SC-5).
ALTER TABLE task_quality_config ADD COLUMN success_threshold REAL;

-- ── output_quality: add task_category column ─────────────────────────────────

ALTER TABLE output_quality ADD COLUMN task_category TEXT;

-- ── output_quality: recreate with expanded score_method CHECK ────────────────
-- Original CHECK: ('averaged','conservative_min','needs_review','single_judge')
-- New CHECK adds:  'consensus_median','majority_median'
--
-- SQLite cannot ALTER CHECK constraints directly.
-- Recreate with data preservation (zero rows currently — verified before migration).
-- If rows exist in future: use INSERT INTO new_table SELECT ... FROM old_table.

-- Step 1: rename existing table.
ALTER TABLE output_quality RENAME TO output_quality_old;

-- Step 2: create new table with expanded CHECK.
CREATE TABLE IF NOT EXISTS output_quality (
    quality_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id              INTEGER NOT NULL,
    goal_id                 INTEGER NOT NULL,
    task_id                 TEXT,
    task_category           TEXT,
    metric_type             TEXT NOT NULL CHECK(metric_type IN (
                                'binary','scalar','pairwise','testsuite'
                            )),
    raw_score               REAL,
    normalized_score        REAL,
    pass_fail               INTEGER,
    judge_method            TEXT NOT NULL CHECK(judge_method IN (
                                'exact_match','semantic','llm_judge','unit_test'
                            )),
    judge_count             INTEGER NOT NULL DEFAULT 1,
    agreement_score         REAL,
    score_method            TEXT CHECK(score_method IN (
                                'averaged','conservative_min','consensus_median',
                                'majority_median','needs_review','single_judge'
                            )),
    expected_output         TEXT,
    actual_output           TEXT,
    energy_uj_at_judgment   INTEGER,
    manual_reviewed         INTEGER NOT NULL DEFAULT 0,
    judged_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(attempt_id),
    FOREIGN KEY (attempt_id) REFERENCES goal_attempt(attempt_id),
    FOREIGN KEY (goal_id)    REFERENCES goal_execution(goal_id)
);

-- Step 3: copy data from old table (zero rows currently, but correct pattern).
INSERT INTO output_quality
    (quality_id, attempt_id, goal_id, task_id, task_category, metric_type,
     raw_score, normalized_score, pass_fail, judge_method, judge_count,
     agreement_score, score_method, expected_output, actual_output,
     energy_uj_at_judgment, manual_reviewed, judged_at)
SELECT
    quality_id, attempt_id, goal_id, task_id, NULL, metric_type,
    raw_score, normalized_score, pass_fail, judge_method, judge_count,
    agreement_score, score_method, expected_output, actual_output,
    energy_uj_at_judgment, manual_reviewed, judged_at
FROM output_quality_old;

-- Step 4: restore indexes.
CREATE INDEX IF NOT EXISTS idx_output_qual_attempt
    ON output_quality(attempt_id);
CREATE INDEX IF NOT EXISTS idx_output_qual_goal
    ON output_quality(goal_id);
CREATE INDEX IF NOT EXISTS idx_output_qual_metric
    ON output_quality(metric_type);
CREATE INDEX IF NOT EXISTS idx_output_qual_score
    ON output_quality(normalized_score);
CREATE INDEX IF NOT EXISTS idx_output_qual_method
    ON output_quality(judge_method);
CREATE INDEX IF NOT EXISTS idx_output_qual_task_category
    ON output_quality(task_category);

-- Step 5: add task_quality_config indexes for new columns.
CREATE INDEX IF NOT EXISTS idx_tqc_metric
    ON task_quality_config(metric_type);
CREATE INDEX IF NOT EXISTS idx_tqc_judge
    ON task_quality_config(judge_method);

-- Step 6: drop old table.
DROP TABLE output_quality_old;

-- ── schema_version entry ─────────────────────────────────────────────────────
INSERT INTO schema_version (version, applied_at, description)
VALUES (90, datetime('now'),
    'Quality config upgrade: n_judges+judge_model_set on task_quality_config, task_category+expanded score_method on output_quality');
