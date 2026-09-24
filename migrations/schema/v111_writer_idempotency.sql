-- migrations/schema/v112_writer_idempotency.sql
-- 39.2 WP-2a: plumbing table for WriterSession idempotency keys (D6.3a).
-- Not used by existing code in this phase; reserved for 39.7 replay.
-- Additive only — no existing table touched.

CREATE TABLE IF NOT EXISTS writer_idempotency (
    idem_key    TEXT    NOT NULL PRIMARY KEY,
    recorded_at TEXT    NOT NULL DEFAULT (datetime('now'))
);


