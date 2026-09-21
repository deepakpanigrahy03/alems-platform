-- migrations/schema/v109_state_reuse.sql
--
-- 8.6-B2: State Reuse and Cache Telemetry
-- Creates: state_reuse_taxonomy, state_reuse_events,
--          cache_state_snapshots, v_state_reuse_impact
--
-- Depends on: v088_recovery_events.sql (recovery_events FK)
-- MSC-4: DDL only. No INSERT statements in this file.
-- MSC-1: Immutable after first commit. Fix forward.

-- ─────────────────────────────────────────────────
-- 1.  State reuse taxonomy
--     Lookup table seeded in migrations/seed/v109_state_reuse_taxonomy.sql
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS state_reuse_taxonomy (
    reuse_type_id   TEXT PRIMARY KEY,
    description     TEXT,
    -- layer: which system layer owns this cache type
    -- serving   = the LLM serving engine (vLLM, llama.cpp, remote API)
    -- framework = the agent orchestration framework (LangChain, custom)
    -- application = A-LEMS application layer (tool result cache, plan cache)
    layer           TEXT NOT NULL CHECK(layer IN ('serving', 'framework', 'application'))
);

-- ─────────────────────────────────────────────────
-- 2.  State reuse events
--     One row per recovery where per-request token reuse can be measured.
--     Empty on platforms without request-level cache visibility (B2.4, B2.5).
--     FK to recovery_events: only populated when B1 row exists for the recovery.
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS state_reuse_events (
    reuse_id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- FK to recovery_events. NULL acceptable: runner may record reuse
    -- without a preceding recovery (e.g. warm-start baseline measurement).
    recovery_id           INTEGER REFERENCES recovery_events(recovery_id),

    -- FK to goal_attempt for direct join without going through recovery_events.
    attempt_id            INTEGER REFERENCES goal_attempt(attempt_id),

    -- FK to runs for energy join path (P5 attribution chain).
    run_id                INTEGER REFERENCES runs(run_id),

    -- What kind of state was reused.
    reuse_type            TEXT NOT NULL REFERENCES state_reuse_taxonomy(reuse_type_id),

    -- Human-readable identifier for the cache source (e.g. engine name, layer id).
    -- NULL when source is implicit (only one cache of this type exists).
    reuse_source          TEXT,

    -- Tokens the engine reported as reused (not recomputed).
    -- NULL when engine does not expose token-level metrics.
    tokens_reused         INTEGER CHECK(tokens_reused IS NULL OR tokens_reused >= 0),

    -- Tokens the engine fully recomputed for this recovery.
    -- NULL when engine does not expose token-level metrics.
    tokens_recomputed     INTEGER CHECK(tokens_recomputed IS NULL OR tokens_recomputed >= 0),

    -- tokens_reused / (tokens_reused + tokens_recomputed).
    -- NULL when token counts are unavailable.
    reuse_fraction        REAL CHECK(reuse_fraction IS NULL OR reuse_fraction BETWEEN 0 AND 1),

    -- 1 = cache hit (at least partial reuse occurred), 0 = cache miss, NULL = unknown.
    cache_hit             INTEGER CHECK(cache_hit IS NULL OR cache_hit IN (0, 1)),

    -- Wall-clock time to query the cache (nanoseconds). NULL when not instrumented.
    cache_query_time_ns   INTEGER,

    -- Size of preserved state in bytes. NULL when engine does not report it.
    state_size_bytes      INTEGER CHECK(state_size_bytes IS NULL OR state_size_bytes >= 0),

    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sre_recovery  ON state_reuse_events(recovery_id);
CREATE INDEX IF NOT EXISTS idx_sre_attempt   ON state_reuse_events(attempt_id);
CREATE INDEX IF NOT EXISTS idx_sre_run       ON state_reuse_events(run_id);
CREATE INDEX IF NOT EXISTS idx_sre_type      ON state_reuse_events(reuse_type);

-- ─────────────────────────────────────────────────
-- 3.  Cache state snapshots
--     Engine-level aggregate cache metrics — NOT per-recovery.
--     Answers: "what was the cache state at this moment?"
--     Separate from state_reuse_events per B2.3: aggregate metrics MUST NOT
--     be interpreted as recovery-level reuse (B2.4).
-- ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cache_state_snapshots (
    snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,

    -- FK to runs for correlation with energy and attempt records.
    run_id                INTEGER REFERENCES runs(run_id),

    -- FK to goal_attempt: snapshot taken during this attempt.
    attempt_id            INTEGER REFERENCES goal_attempt(attempt_id),

    -- Nanosecond wall-clock timestamp of the snapshot.
    timestamp_ns          INTEGER,

    -- Name of the serving engine at snapshot time (e.g. 'vllm', 'groq', 'llama_cpp').
    engine_name           TEXT,

    -- Cache type as reported by the engine (e.g. 'kv_cache', 'prefix_cache').
    -- Free text: engines name their caches differently. Not FK to taxonomy.
    cache_type            TEXT,

    -- Maximum number of tokens the cache can hold. NULL when engine does not report.
    capacity_tokens       INTEGER CHECK(capacity_tokens IS NULL OR capacity_tokens >= 0),

    -- Current number of tokens occupying the cache. NULL when engine does not report.
    occupied_tokens       INTEGER CHECK(occupied_tokens IS NULL OR occupied_tokens >= 0),

    -- occupied_tokens / capacity_tokens. NULL when either count is unavailable.
    occupancy_fraction    REAL CHECK(occupancy_fraction IS NULL OR occupancy_fraction BETWEEN 0 AND 1),

    -- Aggregate hit rate reported by the engine since last reset.
    -- This is engine-global, not request-scoped. See B2.4.
    hit_rate_aggregate    REAL CHECK(hit_rate_aggregate IS NULL OR hit_rate_aggregate BETWEEN 0 AND 1),

    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_css_run      ON cache_state_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_css_attempt  ON cache_state_snapshots(attempt_id);

-- ─────────────────────────────────────────────────
-- 4.  v_state_reuse_impact
--     Stephen's primary analysis view.
--     Joins state_reuse_events with recovery_events to show per-recovery:
--     what was reused, rollback depth, and recovery energy cost.
--     LEFT JOIN: recovery rows with no reuse data still appear (all NULL reuse cols).
-- ─────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS v_state_reuse_impact AS
SELECT
    re.recovery_id,
    re.attempt_id,
    re.rollback_depth_turns,
    re.recovery_strategy,
    re.replay_fraction,
    -- Recovery energy in joules for human-readable comparison.
    re.recovery_energy_uj / 1e6          AS recovery_energy_j,
    sre.reuse_id,
    sre.reuse_type,
    sre.reuse_source,
    sre.tokens_reused,
    sre.tokens_recomputed,
    sre.reuse_fraction                   AS state_reuse_fraction,
    sre.cache_hit,
    sre.cache_query_time_ns,
    sre.state_size_bytes
FROM recovery_events re
LEFT JOIN state_reuse_events sre
    ON re.recovery_id = sre.recovery_id
ORDER BY
    re.rollback_depth_turns,
    sre.reuse_type;
