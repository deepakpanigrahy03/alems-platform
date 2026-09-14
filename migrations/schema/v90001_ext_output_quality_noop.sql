-- =============================================================================
-- ext-output-quality / e001_create_output_quality.sql
-- Extension migration: Output Quality tables
-- =============================================================================
--
-- Creates tables for the qEpG (quality Energy per Goal) research extension.
-- This migration runs ONLY on machines where output_quality is listed in
-- [extensions] active in app_settings.yaml.
--
-- Tables created:
--   output_quality        — per-run quality scores from LLM-as-judge evaluation
--   run_quality           — summary quality metrics per run
--
-- Note: hallucination_events, output_quality_judges, eval_criteria, and
-- task_quality_config already exist on GN100 from pre-35D schema migrations.
-- They are listed in the existing CREATE TABLE IF NOT EXISTS in schema.py.
-- This extension migration creates only the tables that were NOT created
-- by legacy migrations, preventing duplication.
--
-- All foreign keys reference core tables (always present). Safe to activate
-- on any machine regardless of previous migration history.
--
-- Rule MSC-4: DDL only in this file (no INSERT/UPDATE).
-- =============================================================================

CREATE TABLE IF NOT EXISTS output_quality (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL,
    task_id          TEXT,
    scorer_name      TEXT NOT NULL,
    score            REAL,
    scorer_energy_uj INTEGER,
    evaluated_at     TEXT NOT NULL,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS run_quality (
    run_id                 INTEGER PRIMARY KEY,
    avg_score              REAL,
    scorer_count           INTEGER,
    total_scorer_energy_uj INTEGER,
    computed_at            TEXT NOT NULL
);
