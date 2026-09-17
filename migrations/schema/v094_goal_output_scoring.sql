-- v094_goal_output_scoring.sql
-- SPEC 35J: goal_output table + registry-driven judge_method +
-- UNIQUE(attempt_id) removal + Problem 8 FK fix.
--
-- Applied via: python3 scripts/tools/alems_migrate.py  (MSC-2 — never raw sqlite3)
-- The tool wraps this whole file in its own BEGIN IMMEDIATE / COMMIT via
-- conn.executescript(), records version+checksum in migration_history
-- itself, and runs under PRAGMA legacy_alter_table=ON automatically.
-- Do NOT add BEGIN/COMMIT, .bail, PRAGMA foreign_keys, or any
-- migration_history/schema_version bookkeeping to this file — the tool
-- owns all of that (see apply_one() in scripts/tools/alems_migrate.py).
--
-- Built from the REAL confirmed schema (gn100, pre-migration):
--   output_quality: 20 rows, has task_id + task_category + idx_output_qual_task_category
--   output_quality_judges: 20 rows, DDL unchanged in content, but must be
--     dropped and recreated around the output_quality rebuild because
--     the tool runs with foreign_keys=ON (its FK blocks dropping the
--     parent table otherwise). Data is parked and restored, not lost.
--   task_quality_config: 15 rows, broken FK (Problem 8)
--   v_quality_energy_frontier: VIEW depending on output_quality — dropped
--     before the table is dropped, recreated after (identical DDL). The
--     tool's own legacy_alter_table comment claims no such view exists;
--     that comment is stale — this view is real and must be handled
--     explicitly, which this file does.
--
-- Per COMPLIANCE.md MSC-6: DDL only. No INSERT/UPDATE — Fix 2's data
-- normalization is s010_normalize_judge_method.sql (migrations/seed/),
-- a separate file, separate version series.
-- Per COMPLIANCE.md MSC-3: core/database/schema.py and
-- core/database/sqlite_adapter.py must be updated to match (separate
-- find/replace edits, not part of this file).

-- Defensive guards: if a previous attempt failed partway (this tool has
-- shown it does not always roll back DDL cleanly), these ensure a retry
-- starts from a known-clean state instead of erroring on leftover temp
-- objects. Safe on a truly clean database too — all are no-ops there.
DROP TABLE IF EXISTS _output_quality_new;
DROP TABLE IF EXISTS _output_quality_judges_backup;
DROP TABLE IF EXISTS _task_quality_config_new;
DROP TABLE IF EXISTS goal_output;

-- ============================================================
-- Step 1: goal_output (new table, Problem 6)
-- ============================================================

CREATE TABLE IF NOT EXISTS goal_output (
    goal_id        INTEGER PRIMARY KEY
                   REFERENCES goal_execution(goal_id),
    run_id         INTEGER NOT NULL
                   REFERENCES runs(run_id),
    attempt_id     INTEGER NOT NULL
                   REFERENCES goal_attempt(attempt_id),
    output_text    TEXT,
    output_type    TEXT NOT NULL DEFAULT 'answer'
                   CHECK (output_type IN (
                       'answer','failure','timeout',
                       'context_overflow','api_error','empty'
                   )),
    capture_method TEXT NOT NULL DEFAULT 'forward'
                   CHECK (capture_method IN (
                       'forward','backfill_inferred','manual'
                   )),
    step_index     INTEGER,
    captured_at    TEXT NOT NULL
                   DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ============================================================
-- Step 2: drop the dependent view BEFORE touching output_quality
-- ============================================================

DROP VIEW IF EXISTS v_quality_energy_frontier;

-- ============================================================
-- Step 2b: output_quality_judges has an FK to output_quality(quality_id).
-- The tool runs with PRAGMA foreign_keys=ON, so dropping output_quality
-- below would be blocked while this child table still references it.
-- Park its data in a plain table, drop it, restore after output_quality
-- is rebuilt. Its own DDL is unchanged by this migration.
-- ============================================================

CREATE TABLE _output_quality_judges_backup AS SELECT * FROM output_quality_judges;
DROP TABLE IF EXISTS output_quality_judges;

-- ============================================================
-- Step 3: output_quality — recreate with REAL confirmed columns,
-- minus judge_method CHECK (Fix 1), minus UNIQUE(attempt_id) (Fix 2),
-- plus scorer_version/scorer_config_hash, plus back_scored/stub_skipped
-- added to score_method CHECK.
-- ============================================================

CREATE TABLE _output_quality_new (
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
    judge_method            TEXT NOT NULL,
    judge_count             INTEGER NOT NULL DEFAULT 1,
    agreement_score         REAL,
    score_method            TEXT CHECK(score_method IN (
                                'averaged','conservative_min','consensus_median',
                                'majority_median','needs_review','single_judge',
                                'back_scored','stub_skipped'
                            )),
    expected_output         TEXT,
    actual_output           TEXT,
    energy_uj_at_judgment   INTEGER,
    manual_reviewed         INTEGER NOT NULL DEFAULT 0,
    judged_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scorer_version          TEXT,
    scorer_config_hash      TEXT,
    FOREIGN KEY (attempt_id) REFERENCES goal_attempt(attempt_id),
    FOREIGN KEY (goal_id)    REFERENCES goal_execution(goal_id)
);

INSERT INTO _output_quality_new (
    quality_id, attempt_id, goal_id, task_id, task_category, metric_type,
    raw_score, normalized_score, pass_fail, judge_method, judge_count,
    agreement_score, score_method, expected_output, actual_output,
    energy_uj_at_judgment, manual_reviewed, judged_at
)
SELECT
    quality_id, attempt_id, goal_id, task_id, task_category, metric_type,
    raw_score, normalized_score, pass_fail, judge_method, judge_count,
    agreement_score, score_method, expected_output, actual_output,
    energy_uj_at_judgment, manual_reviewed, judged_at
FROM output_quality;

DROP TABLE output_quality;
ALTER TABLE _output_quality_new RENAME TO output_quality;
-- SQLite's ALTER TABLE RENAME TO automatically updates the
-- sqlite_sequence entry for AUTOINCREMENT tables, so quality_id's
-- counter continues correctly and output_quality_judges' existing
-- quality_id foreign keys remain valid without manual fixup.

CREATE INDEX idx_output_qual_attempt       ON output_quality(attempt_id);
CREATE INDEX idx_output_qual_goal          ON output_quality(goal_id);
CREATE INDEX idx_output_qual_metric        ON output_quality(metric_type);
CREATE INDEX idx_output_qual_score         ON output_quality(normalized_score);
CREATE INDEX idx_output_qual_method        ON output_quality(judge_method);
CREATE INDEX idx_output_qual_task_category ON output_quality(task_category);

-- ============================================================
-- Step 3b: restore output_quality_judges, original DDL, unchanged.
-- Safe now: output_quality exists again with the same quality_id
-- values (autoincrement rename preserves them), so every FK in the
-- restored rows resolves correctly.
-- ============================================================

CREATE TABLE output_quality_judges (
    judge_entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quality_id              INTEGER NOT NULL,
    attempt_id              INTEGER NOT NULL,
    goal_id                 INTEGER NOT NULL,
    judge_model             TEXT NOT NULL,
    judge_provider          TEXT,
    judge_version           TEXT,
    judge_temperature       REAL,
    judge_score             REAL NOT NULL,
    judge_confidence        REAL,
    judge_prompt_hash       TEXT,
    judge_reasoning         TEXT,
    judged_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (quality_id) REFERENCES output_quality(quality_id),
    FOREIGN KEY (attempt_id) REFERENCES goal_attempt(attempt_id),
    FOREIGN KEY (goal_id)    REFERENCES goal_execution(goal_id)
);

INSERT INTO output_quality_judges SELECT * FROM _output_quality_judges_backup;
DROP TABLE _output_quality_judges_backup;

CREATE INDEX idx_oqj_quality ON output_quality_judges(quality_id);
CREATE INDEX idx_oqj_attempt ON output_quality_judges(attempt_id);
CREATE INDEX idx_oqj_goal    ON output_quality_judges(goal_id);

-- ============================================================
-- Step 4: recreate the view, identical DDL, now pointed at the
-- rebuilt output_quality (same name, so no query change needed).
-- ============================================================

CREATE VIEW v_quality_energy_frontier AS
SELECT
    oq.attempt_id,
    oq.goal_id,
    oq.normalized_score,
    oq.metric_type,
    oq.judge_method,
    oq.score_method,
    oq.judge_count,
    ge.total_energy_uj      / 1e6  AS total_goal_energy_j,
    ga.energy_uj            / 1e6  AS attempt_energy_j,
    ga.orchestration_uj     / 1e6  AS attempt_orchestration_j,
    ga.compute_uj           / 1e6  AS attempt_compute_j,
    ga.outcome,
    ge.workflow_type,
    ge.goal_type,
    ge.difficulty_level,
    CASE WHEN oq.normalized_score > 0
         THEN ge.total_energy_uj / oq.normalized_score
         ELSE NULL
    END                            AS energy_per_quality_point_uj,
    e.experiment_type
FROM output_quality oq
JOIN goal_attempt ga   ON oq.attempt_id = ga.attempt_id
JOIN goal_execution ge ON oq.goal_id = ge.goal_id
JOIN experiments e     ON ge.exp_id = e.exp_id
WHERE oq.score_method != 'needs_review'
  AND e.experiment_type IN (
      'normal','overhead_study','retry_study',
      'failure_injection','quality_sweep','ablation','pilot'
  );

-- ============================================================
-- Step 5: task_quality_config — recreate minus judge_method CHECK,
-- minus the broken task_category FK (Problem 8).
-- ============================================================

CREATE TABLE _task_quality_config_new (
    config_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_category     TEXT NOT NULL UNIQUE,
    metric_type       TEXT NOT NULL CHECK(metric_type IN ('binary','scalar','pairwise','testsuite')),
    judge_method      TEXT NOT NULL,
    threshold         REAL NOT NULL DEFAULT 0.80,
    dual_judge        INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    n_judges          INTEGER NOT NULL DEFAULT 1,
    judge_model_set   TEXT,
    rubric            TEXT,
    success_threshold REAL
    -- Problem 8: old FK (task_category -> task_categories.task_id) was
    -- semantically wrong (task_category holds a bucket like "coding";
    -- task_categories.task_id holds individual task names like
    -- "code_fibonacci"). All 15 existing rows already violated it.
    -- task_categories.category is not UNIQUE, so it cannot be a valid
    -- FK target without inventing new relational structure 35J does not
    -- require. Dropped, not repaired. Category validity is a
    -- configuration/seeding-layer concern (scripts/seed_quality_config.py).
);

INSERT INTO _task_quality_config_new
SELECT * FROM task_quality_config;

DROP TABLE task_quality_config;
ALTER TABLE _task_quality_config_new RENAME TO task_quality_config;

CREATE INDEX idx_tqc_category ON task_quality_config(task_category);
CREATE INDEX idx_tqc_method   ON task_quality_config(judge_method);

-- ============================================================
-- Verification (run manually AFTER `python3 scripts/tools/alems_migrate.py`):
--
--   python3 scripts/tools/alems_migrate.py --check
--   python3 scripts/tools/alems_migrate.py --verify
--   sqlite3 $DB "PRAGMA foreign_key_check;"
--     Expect: 0 task_quality_config rows. goal_attempt/runs violations
--     are pre-existing and unrelated to this migration.
--   sqlite3 $DB "SELECT COUNT(*) FROM output_quality;"        -- expect 20
--   sqlite3 $DB "SELECT COUNT(*) FROM output_quality_judges;" -- expect 20
--   sqlite3 $DB "SELECT COUNT(*) FROM task_quality_config;"   -- expect 15
--   sqlite3 $DB "SELECT * FROM v_quality_energy_frontier LIMIT 3;"
--
-- Post-migration (seed s010 applies automatically in the same
-- alems_migrate.py run, per its pending_seed loop):
--   python3 scripts/seed_quality_config.py   -- inserts translation/media rows
--   python3 scripts/detect_environment.py    -- regenerate env_hash (SC-7)
--   python3 scripts/backfill_goal_output.py
-- ============================================================
