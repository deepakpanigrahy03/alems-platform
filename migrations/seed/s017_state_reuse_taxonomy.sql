-- migrations/seed/v109_state_reuse_taxonomy.sql
--
-- Seed: state_reuse_taxonomy
-- Six canonical reuse types across three layers.
--
-- MSC-4: seed data only. No DDL in this file.
-- MSC-1: Immutable after first commit. Fix forward.
-- P1: adding a new reuse type = one INSERT here, zero code changes.

INSERT OR IGNORE INTO state_reuse_taxonomy (reuse_type_id, description, layer) VALUES
    -- Serving layer: managed by the LLM engine, not A-LEMS.
    ('prefix_cache',
     'Serving-layer prefix/prompt cache reuse. Engine detects common prompt prefix and skips recomputation.',
     'serving'),

    ('kv_cache',
     'Serving-layer KV cache block reuse. Key-value attention blocks preserved across requests.',
     'serving'),

    -- Framework layer: managed by the agent orchestration framework.
    ('context_window',
     'Framework-layer context window preservation. Prior conversation turns kept in context on retry.',
     'framework'),

    -- Application layer: managed by A-LEMS or the agent application itself.
    ('tool_result_cache',
     'Application-layer cached tool results. Tool outputs from prior turns reused without re-invocation.',
     'application'),

    ('plan_cache',
     'Application-layer cached agent plans. Planning phase output reused when rolling back to execution phase only.',
     'application'),

    ('checkpoint_state',
     'Application-layer checkpoint/resume state. Full agent state snapshot restored to avoid full replay.',
     'application');
