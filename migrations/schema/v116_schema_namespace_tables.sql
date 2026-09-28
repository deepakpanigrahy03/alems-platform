-- v116_schema_namespace_tables.sql
-- Records which namespace owns each non-core table and view.
-- No DDL changes to existing tables. Additive only (Rule S, SC-5).
-- After this migration, future DDL for namespace tables goes only into
-- migrations/extensions/<namespace>/ not into migrations/schema/.

CREATE TABLE IF NOT EXISTS schema_namespace_tables (
    namespace   TEXT NOT NULL,
    object_name TEXT NOT NULL,
    object_kind TEXT NOT NULL CHECK (object_kind IN ('table', 'view')),
    adopted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (namespace, object_name)
);

INSERT INTO schema_version (version, applied_at, description)
VALUES (116, datetime('now'), 'schema_namespace_tables: records namespace ownership of non-core tables and views');
