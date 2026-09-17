-- =============================================================================
-- ext-tool-selection / e001_create_tool_selection_events.sql
-- Extension migration: Tool Selection Events table
-- =============================================================================
--
-- Creates the table for recording retrieval-based tool selection events
-- (SPEC 35I). This migration runs ONLY on machines where tool_selection
-- is listed in [extensions] active in app_settings.yaml.
--
-- Table created:
--   tool_selection_events — one row per RetrievalToolSelector.select()
--                            call, recording selection energy, k/actual_k,
--                            and cache state.
--
-- WRITE PATH (SPEC 35H CR-4, restated): RetrievalToolSelector.select()
-- writes to this table DIRECTLY, at selection time, reusing the live db
-- connection from ToolSelectionContext.db. This extension's own
-- on_post_run() is a documented no-op (see extension.py) — selection
-- happens before a run is committed, so the normal post-run hook cannot
-- be the write path here. This is specific to this extension's timing
-- constraint, not a general pattern for other extensions.
--
-- energy_provenance is always "measured_interval" (SPEC 35I v2 Section
-- 1/3) — selector_energy_uj is a measured wall-clock energy delta around
-- the selection call, not a verified component-attributed quantity.
-- cache_hit / tools_embedded_count (SPEC 35I v2.1) separate one-time
-- index-construction energy from steady-state retrieval energy — do not
-- average selector_energy_uj across rows without checking these first.
--
-- Rule MSC-4: DDL only in this file (no INSERT/UPDATE).
-- =============================================================================

CREATE TABLE IF NOT EXISTS tool_selection_events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 INTEGER,
    selector_type          TEXT NOT NULL,
    available_count        INTEGER NOT NULL,
    requested_k            INTEGER NOT NULL,
    actual_k               INTEGER NOT NULL,
    selected_tool_names    TEXT,
    selector_energy_uj     INTEGER,
    energy_provenance      TEXT NOT NULL DEFAULT 'measured_interval',
    duration_ns            INTEGER,
    embedding_model        TEXT,
    embedding_dimensions   INTEGER,
    cache_hit              INTEGER NOT NULL DEFAULT 0,
    tools_embedded_count   INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL
);
