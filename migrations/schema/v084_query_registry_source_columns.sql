-- v084: Add source_yaml, source_tab, version columns to query_registry
-- Required by migrate_yaml_to_db.py. Safe to run multiple times via
-- ALTER TABLE which fails silently if column already exists on SQLite.
ALTER TABLE query_registry ADD COLUMN source_yaml TEXT;
ALTER TABLE query_registry ADD COLUMN source_tab  TEXT;
ALTER TABLE query_registry ADD COLUMN version     TEXT DEFAULT '1.0';
