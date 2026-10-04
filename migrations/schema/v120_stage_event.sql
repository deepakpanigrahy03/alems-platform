-- v120: C-EV stage events and declared stage graphs (contract plumbing, master 5.1, design 39.5.2 addendum)
-- DDL only (MSC-4). Additive. 39.5.5 continues stage_event as stage_run by adding columns only.

CREATE TABLE IF NOT EXISTS stage_graph (
    graph_hash     TEXT PRIMARY KEY,          -- SHA-256 of canonical definition
    graph_id       TEXT NOT NULL,             -- save_pair | save_single | execute_goal
    graph_version  TEXT NOT NULL,             -- semver; immutable per version
    definition     TEXT NOT NULL,             -- canonical JSON (nodes, edges)
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_event (
    event_id        TEXT PRIMARY KEY,         -- UUID assigned in memory
    run_uid         TEXT NOT NULL,            -- identity (master 13.2)
    run_id          INTEGER REFERENCES runs(run_id),  -- nullable link (EEI-4)
    sandbox_id      TEXT,
    stage_id        TEXT NOT NULL CHECK (stage_id IN ('setup','baseline','measure','persist_run',
                        'persist_samples','spans','attribution','residual','quality','hooks',
                        'etl_phase','etl_hardware','integrity','outputs')),
    stage_version   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed',
                        'skipped','skipped_dependency')),
    outcome         TEXT CHECK (outcome IS NULL OR outcome IN ('empty','unavailable','partial')),
    reason          TEXT,
    event_seq       INTEGER,                  -- per process gate counter
    pid             INTEGER NOT NULL,
    start_ns        INTEGER,
    end_ns          INTEGER,
    counts          TEXT,                     -- JSON object table -> rows
    error_ref       TEXT,                     -- C-ERR error_id (2d)
    parent_stage_id TEXT,
    graph_hash      TEXT NOT NULL REFERENCES stage_graph(graph_hash),
    blocked_by      TEXT,                     -- stage_id causing skipped_dependency
    scope           TEXT NOT NULL DEFAULT 'run' CHECK (scope IN ('run','pair','goal')),
    created_at      TEXT NOT NULL,
    UNIQUE (run_uid, stage_id)
);

CREATE INDEX IF NOT EXISTS idx_stage_event_run_id ON stage_event(run_id);
CREATE INDEX IF NOT EXISTS idx_stage_event_status ON stage_event(status);
