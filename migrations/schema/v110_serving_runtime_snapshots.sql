-- =============================================================================
-- v110: serving_runtime_snapshots
-- =============================================================================
-- Schema version: 110
-- Depends on: v109 (state_reuse_taxonomy, state_reuse_events,
--                   cache_state_snapshots)
-- MSC-1: immutable after first commit. Fix forward only.
-- MSC-4: DDL only — no INSERT/UPDATE/DELETE here.
--
-- PURPOSE:
--   Generic point-in-time snapshot table for any serving engine runtime
--   metric that is NOT per-request KV cache reuse (that is state_reuse_events).
--
--   cache_state_snapshots (B2) records KV cache occupancy/hit-rate only.
--   This table records EVERYTHING ELSE an engine exposes as a time-stamped
--   aggregate:
--     - KV cache occupancy (from engines that also expose it here)
--     - Expert tier placement (Colibri VRAM/RAM/NVMe expert working set)
--     - Queue depth / health counters
--     - Token rate telemetry
--
-- WHY SEPARATE FROM cache_state_snapshots:
--   Expert tier (Colibri) = model-weight placement across storage hierarchy.
--   KV cache = request-context state.
--   These are semantically different objects and must never be conflated.
--   Mixing them in one table would corrupt Stephen's recovery analysis.
--   See review point 2 in architecture review 2026-09-22.
--
-- snapshot_type values:
--   'kv_cache'      — KV cache aggregate (vLLM, SGLang, llama.cpp)
--   'expert_tier'   — MoE expert placement tiers (Colibri)
--   'queue'         — request queue / health counters (all engines)
--   'token_rate'    — throughput telemetry (all engines)
--
-- telemetry_scope values (from ServingCapabilities.telemetry_scope):
--   'request'       — metric is per-request (strongest attribution)
--   'interval'      — Prometheus delta over a time window
--   'process'       — engine-global since last reset
--   'unavailable'   — engine does not expose this metric
-- =============================================================================

CREATE TABLE IF NOT EXISTS serving_runtime_snapshots (
    snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,

    -- execution context
    run_id                INTEGER REFERENCES runs(run_id),
    attempt_id            INTEGER REFERENCES goal_attempt(attempt_id),
    timestamp_ns          INTEGER NOT NULL,

    -- engine identity
    engine_name           TEXT NOT NULL,
    engine_type           TEXT NOT NULL,

    -- what kind of snapshot this row represents
    snapshot_type         TEXT NOT NULL CHECK(snapshot_type IN (
                              'kv_cache', 'expert_tier', 'queue', 'token_rate'
                          )),

    -- provenance: at what granularity was this measured?
    telemetry_scope       TEXT NOT NULL CHECK(telemetry_scope IN (
                              'request', 'interval', 'process', 'unavailable'
                          )),

    -- ── KV cache fields (snapshot_type = 'kv_cache') ──────────────────────
    -- NULL for non-KV rows. Do not populate for expert_tier rows.
    kv_capacity_tokens    INTEGER CHECK(kv_capacity_tokens IS NULL
                              OR kv_capacity_tokens >= 0),
    kv_occupied_tokens    INTEGER CHECK(kv_occupied_tokens IS NULL
                              OR kv_occupied_tokens >= 0),
    kv_occupancy_fraction REAL    CHECK(kv_occupancy_fraction IS NULL
                              OR kv_occupancy_fraction BETWEEN 0 AND 1),
    kv_hit_rate_aggregate REAL    CHECK(kv_hit_rate_aggregate IS NULL
                              OR kv_hit_rate_aggregate BETWEEN 0 AND 1),
    kv_num_evictions      INTEGER CHECK(kv_num_evictions IS NULL
                              OR kv_num_evictions >= 0),

    -- ── Expert tier fields (snapshot_type = 'expert_tier') ────────────────
    -- Colibri VRAM/RAM/NVMe expert working-set placement.
    -- NULL for non-expert rows. MUST NOT be populated for kv_cache rows.
    tier_vram_bytes       INTEGER CHECK(tier_vram_bytes IS NULL
                              OR tier_vram_bytes >= 0),
    tier_ram_bytes        INTEGER CHECK(tier_ram_bytes IS NULL
                              OR tier_ram_bytes >= 0),
    tier_disk_bytes       INTEGER CHECK(tier_disk_bytes IS NULL
                              OR tier_disk_bytes >= 0),
    tier_vram_fraction    REAL    CHECK(tier_vram_fraction IS NULL
                              OR tier_vram_fraction BETWEEN 0 AND 1),
    tier_ram_fraction     REAL    CHECK(tier_ram_fraction IS NULL
                              OR tier_ram_fraction BETWEEN 0 AND 1),
    tier_disk_fraction    REAL    CHECK(tier_disk_fraction IS NULL
                              OR tier_disk_fraction BETWEEN 0 AND 1),

    -- ── Queue / health fields (snapshot_type = 'queue') ───────────────────
    -- Colibri /health, vLLM queue depth, SGLang running count.
    queue_active          INTEGER CHECK(queue_active IS NULL
                              OR queue_active >= 0),
    queue_waiting         INTEGER CHECK(queue_waiting IS NULL
                              OR queue_waiting >= 0),
    queue_completed       INTEGER CHECK(queue_completed IS NULL
                              OR queue_completed >= 0),
    queue_rejected        INTEGER CHECK(queue_rejected IS NULL
                              OR queue_rejected >= 0),

    -- ── Token rate fields (snapshot_type = 'token_rate') ──────────────────
    tokens_per_second     REAL    CHECK(tokens_per_second IS NULL
                              OR tokens_per_second >= 0),
    ttft_ms               REAL    CHECK(ttft_ms IS NULL OR ttft_ms >= 0),
    prompt_tokens_total   INTEGER CHECK(prompt_tokens_total IS NULL
                              OR prompt_tokens_total >= 0),
    generation_tokens_total INTEGER CHECK(generation_tokens_total IS NULL
                              OR generation_tokens_total >= 0),

    -- ── Generic overflow ───────────────────────────────────────────────────
    -- Engine-specific fields not yet modeled above.
    -- JSON object. NULL when nothing extra. Never use to smuggle
    -- fields that belong in the typed columns above.
    extra_json            TEXT,

    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_srs_run
    ON serving_runtime_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_srs_attempt
    ON serving_runtime_snapshots(attempt_id);
CREATE INDEX IF NOT EXISTS idx_srs_engine_type
    ON serving_runtime_snapshots(engine_type, snapshot_type);
CREATE INDEX IF NOT EXISTS idx_srs_timestamp
    ON serving_runtime_snapshots(timestamp_ns);
