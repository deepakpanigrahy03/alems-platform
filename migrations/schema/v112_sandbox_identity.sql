-- v112_sandbox_identity.sql
-- DDL only (MSC-4). Data inserted by alems sandbox create/adopt commands.
-- Tracks every sandbox that has ever written to this store.
-- sandbox_id is the uuid from alems-sandbox.yaml (never changes, MSC-5 equivalent).
-- adopted_from is the legacy store path when this store was adopted rather than created fresh.

CREATE TABLE IF NOT EXISTS sandbox_identity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sandbox_id       TEXT    NOT NULL UNIQUE,   -- uuid from alems-sandbox.yaml
    name             TEXT    NOT NULL,           -- human name e.g. default-dev
    sandbox_path     TEXT    NOT NULL,           -- absolute path to sandbox repo
    engine_version   TEXT    NOT NULL,           -- e.g. 1.0.0
    engine_python    TEXT    NOT NULL,           -- absolute path to venv python
    adopted_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    adopted_from     TEXT    NULL                -- legacy store path if adopted, else NULL
);
