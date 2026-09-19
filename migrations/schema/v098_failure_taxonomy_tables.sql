-- =============================================================================
-- v098_failure_taxonomy_tables.sql
-- SPEC 8.6-A1: Create failure_taxonomy and recovery_taxonomy tables.
-- These are core lookup tables — core FKs in tool_failure_events and
-- goal_attempt reference them directly, so they must exist before v099.
-- MSC-4: DDL only. Seed data is in migrations/seed/s014_failure_taxonomy_seed.sql.
-- Prerequisite: none (new tables, additive only).
-- =============================================================================

-- failure_taxonomy
-- One row per canonical failure type.
-- failure_type_id is the stable TEXT primary key referenced by FKs in
-- tool_failure_events and goal_attempt after v099 reconstruction.
-- domain groups failure types into six coarse families for cross-paper analysis.
-- default_retryable and default_recovery_strategy are advisory defaults;
-- ear_policy_rules (A4) can override per-policy.
-- typical_cost_rank is a seed-time advisory ordering — not computed from data.
CREATE TABLE IF NOT EXISTS failure_taxonomy (
    failure_type_id         TEXT PRIMARY KEY,
    domain                  TEXT NOT NULL CHECK(domain IN (
                                'reasoning', 'execution', 'communication',
                                'resource', 'validation', 'platform_specific'
                            )),
    description             TEXT,
    default_retryable       INTEGER NOT NULL DEFAULT 1
                            CHECK(default_retryable IN (0, 1)),
    default_recovery_strategy TEXT,
    typical_cost_rank       INTEGER,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- recovery_taxonomy
-- One row per canonical recovery strategy.
-- strategy_id is the stable TEXT primary key referenced by FKs in
-- tool_failure_events after v099 reconstruction.
-- requires_state_preservation = 1 means the recovery strategy needs the
-- orchestration state from the failed attempt to be held in memory.
-- typical_rollback_depth: 0 = no rollback, N = N turns back, -1 = full restart.
CREATE TABLE IF NOT EXISTS recovery_taxonomy (
    strategy_id                 TEXT PRIMARY KEY,
    description                 TEXT,
    requires_state_preservation INTEGER DEFAULT 0
                                CHECK(requires_state_preservation IN (0, 1)),
    typical_rollback_depth      INTEGER,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index on domain for cross-domain failure analysis queries (Paper 8 H3, H4).
CREATE INDEX IF NOT EXISTS idx_failure_taxonomy_domain
    ON failure_taxonomy(domain);
