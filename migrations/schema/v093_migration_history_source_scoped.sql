-- =============================================================================
-- v093_migration_history_source_scoped.sql
-- Rebuild migration_history with UNIQUE(version, type, source)
-- =============================================================================
--
-- ROOT CAUSE:
--   Extension migrations computed a synthetic global version number
--   (90000 + local_version) with no extension identity folded in at all.
--   Every extension's first migration (e001 -> local version 1) computed
--   to the identical value 90001, regardless of which extension it was.
--   output_quality occupied it first only because it was the only
--   extension that had ever shipped a migration until a second one
--   (tool_selection) collided with it.
--
--   Full root cause, rejected fixes, and design: see
--   MIGRATION_HISTORY_SOURCE_SCOPED_UNIQUENESS.md (v3).
--
-- FIX:
--   Extension migration identity becomes (source, local_version, type)
--   instead of a single manufactured global integer. Each extension owns
--   an independent local sequence (e001, e002, ...) — two different
--   extensions can both legitimately have e001, with zero coordination
--   needed between plugin authors.
--
-- SAFETY:
--   This rebuild changes ONLY the UNIQUE constraint. No historical data
--   is modified or reinterpreted. output_quality's existing row
--   (version=90001, source='ext:output_quality') is preserved exactly
--   as-is — grandfathered permanently, per design doc Section 6.5. Its
--   own "already applied" check keys on (source, filename), never on
--   version, so this asymmetry is harmless and requires no fleet-wide
--   coordination to fix cosmetically.
--
--   Confirmed safe by direct transaction test before this file was
--   written: SQLite DDL (CREATE TABLE / DROP TABLE) participates fully
--   in the enclosing transaction on this SQLite build — a failure at
--   any point during this migration rolls back cleanly with the
--   original table fully intact, verified empirically, not assumed.
--
-- Rule MSC-4: DDL only in this file. The INSERT below is a data-
-- preserving copy of every existing row, not new data.
-- =============================================================================

CREATE TABLE migration_history_new (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    version            INTEGER NOT NULL,
    type               TEXT NOT NULL CHECK(type IN ('schema', 'seed')),
    filename           TEXT NOT NULL,
    checksum_sha256    TEXT NOT NULL,
    applied_at         TEXT NOT NULL DEFAULT (datetime('now')),
    tool_version       TEXT NOT NULL,
    duration_ms        INTEGER NOT NULL,
    status             TEXT NOT NULL CHECK(status IN ('pending', 'running', 'applied', 'failed')),
    hostname           TEXT NOT NULL,
    machine_id         TEXT,
    repo_commit        TEXT,
    original_checksum  TEXT,
    healed_at          TEXT,
    source             TEXT DEFAULT 'core',
    UNIQUE(version, type, source)
);

INSERT INTO migration_history_new
    (id, version, type, filename, checksum_sha256, applied_at, tool_version,
     duration_ms, status, hostname, machine_id, repo_commit, original_checksum,
     healed_at, source)
SELECT
    id, version, type, filename, checksum_sha256, applied_at, tool_version,
    duration_ms, status, hostname, machine_id, repo_commit, original_checksum,
    healed_at, source
FROM migration_history;

DROP TABLE migration_history;

ALTER TABLE migration_history_new RENAME TO migration_history;
