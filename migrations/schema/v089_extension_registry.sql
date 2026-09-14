-- =============================================================================
-- v089_extension_registry.sql
-- Core migration: Extension Registry + migration_history source column
-- =============================================================================
--
-- Creates the extension_registry table that tracks which research extensions
-- are activated on this machine, their version, and migration state.
--
-- Also adds the `source` column to migration_history to distinguish core
-- migrations (source='core') from extension migrations (source='ext:<name>').
--
-- Both changes are additive. No existing tables or columns are modified.
-- Failure of either statement is recoverable.
--
-- Rule SC-7: schema_version entry added below.
-- =============================================================================

CREATE TABLE IF NOT EXISTS extension_registry (
    name              TEXT PRIMARY KEY,
    version           TEXT NOT NULL,
    activated_at      TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    migration_version TEXT
);

-- Add source column to migration_history.
-- Tracks whether each migration came from core or an extension.
-- "duplicate column name" is handled by apply_one idempotency logic.
ALTER TABLE migration_history ADD COLUMN source TEXT DEFAULT 'core';

INSERT INTO schema_version (version, applied_at, description)
VALUES (89, datetime('now'), 'Add extension_registry table and source column to migration_history');
