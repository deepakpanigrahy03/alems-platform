-- =============================================================================
-- s009_retroactive_extension_tags.sql
-- Seed migration: Tag existing extension tables as 'legacy' in extension_registry
-- =============================================================================
--
-- On existing machines (GN100, Lenovo, AMD), all 35 extension tables already
-- exist because they were created by pre-35D schema migrations. These machines
-- have never had an [extensions] section in app_settings.yaml, so they run in
-- legacy mode where all extension tables receive data as before.
--
-- This seed migration inserts a row for each of the 11 extensions with
-- status='legacy', recording that these tables exist but were not explicitly
-- activated through the new extension system.
--
-- status='legacy' means:
--   - Tables exist (created by earlier schema migrations)
--   - Runtime writes continue through experiment_runner.py direct paths
--   - The extension has NOT been explicitly activated via [extensions] active
--
-- INSERT OR IGNORE: safe to run on fresh machines (no-op if no rows existed).
-- INSERT OR IGNORE: safe to run again on existing machines (idempotent).
--
-- Rule MSC-4: this file contains data only (INSERT). No DDL.
-- =============================================================================

INSERT OR IGNORE INTO extension_registry (name, version, activated_at, status)
VALUES
    ('orchestration',      '1.0.0', datetime('now'), 'legacy'),
    ('failure_recovery',   '1.0.0', datetime('now'), 'legacy'),
    ('output_quality',     '1.0.0', datetime('now'), 'legacy'),
    ('network_energy',     '1.0.0', datetime('now'), 'legacy'),
    ('attribution',        '1.0.0', datetime('now'), 'legacy'),
    ('outlier_detection',  '1.0.0', datetime('now'), 'legacy'),
    ('gui',                '1.0.0', datetime('now'), 'legacy'),
    ('telemetry',          '1.0.0', datetime('now'), 'legacy'),
    ('llm_tracking',       '1.0.0', datetime('now'), 'legacy'),
    ('power_limits',       '1.0.0', datetime('now'), 'legacy'),
    ('task_categories',    '1.0.0', datetime('now'), 'legacy');
