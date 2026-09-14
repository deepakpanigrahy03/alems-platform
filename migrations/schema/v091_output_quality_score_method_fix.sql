-- =============================================================================
-- v091_output_quality_score_method_fix.sql
-- Expand score_method CHECK constraint on output_quality
-- =============================================================================
-- v090 partially applied (ALTER TABLE additions succeeded) but failed on
-- table recreation due to broken v_energy view triggering revalidation.
-- task_category column and n_judges column already exist from v090 ALTERs.
-- This migration completes v090 intent: expand score_method CHECK.
-- Uses DROP+CREATE without RENAME to avoid view revalidation trigger.
-- Zero rows in output_quality — no data loss.
-- =============================================================================

-- Drop existing indexes first (required before DROP TABLE).
DROP INDEX IF EXISTS idx_output_qual_attempt;
DROP INDEX IF EXISTS idx_output_qual_goal;
DROP INDEX IF EXISTS idx_output_qual_metric;
DROP INDEX IF EXISTS idx_output_qual_score;
DROP INDEX IF EXISTS idx_output_qual_method;
DROP INDEX IF EXISTS idx_output_qual_task_category;

-- Drop and recreate with expanded score_method CHECK.
DROP TABLE IF EXISTS output_quality;

CREATE TABLE output_quality (
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

INSERT INTO schema_version (version, applied_at, description)
VALUES (91, datetime('now'),
    'output_quality: expand score_method CHECK (consensus_median, majority_median)');
