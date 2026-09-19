-- =============================================================================
-- s014_failure_taxonomy_seed.sql
-- SPEC 8.6-A1 (A1.8, A1.9): Seed canonical failure and recovery taxonomies.
-- MSC-4: data only — no DDL here. Tables created by v098.
-- These rows are canonical and shared across all platforms (A1.9).
-- Canonical type_ids are immutable once seeded.
-- DELETE or UPDATE on these rows is prohibited by application convention.
-- To extend: INSERT new rows with domain='platform_specific'.
-- Prerequisite: v098 must be applied before this seed runs.
-- =============================================================================

-- ── failure_taxonomy: 13 canonical rows ──────────────────────────────────────
-- Ordered by typical_cost_rank ascending (cheapest to recover first).
-- cost_rank is advisory seed-time estimate; A3 profiling will compute real values.

INSERT OR IGNORE INTO failure_taxonomy
    (failure_type_id, domain, description, default_retryable,
     default_recovery_strategy, typical_cost_rank)
VALUES
    -- reasoning domain: LLM-produced errors — highest recovery cost
    ('hallucination',    'reasoning',
     'LLM produced factually wrong or fabricated output',
     1, 'full_restart', 1),

    ('semantic_error',   'reasoning',
     'LLM output syntactically valid but semantically wrong',
     1, 'turn_retry', 2),

    ('capability_error', 'reasoning',
     'LLM attempted action beyond its capability',
     0, 'abort', 3),

    -- communication domain: auth fails fast, others backoff
    ('auth_error',       'communication',
     'Authentication or authorization failure',
     0, 'abort', 4),

    -- execution domain: transient failures — cheap to recover
    ('timeout',          'execution',
     'Operation exceeded time limit',
     1, 'backoff_retry', 5),

    ('malformed_input',  'execution',
     'Input to tool or LLM was malformed',
     1, 'turn_retry', 6),

    ('tool_error',       'execution',
     'Tool invocation failed during execution',
     1, 'immediate_retry', 7),

    -- validation domain: parse/format errors
    ('malformed_output', 'validation',
     'Output from tool or LLM was malformed (e.g., unparseable JSON)',
     1, 'immediate_retry', 8),

    ('json_parse',       'validation',
     'JSON parsing failed on LLM or tool output',
     1, 'immediate_retry', 9),

    -- communication domain continued
    ('api_error',        'communication',
     'API returned an error response',
     1, 'backoff_retry', 10),

    ('network_error',    'communication',
     'Network connectivity or DNS failure',
     1, 'backoff_retry', 11),

    ('rate_limit',       'communication',
     'API rate limit exceeded',
     1, 'backoff_retry', 12),

    ('not_found',        'communication',
     'Requested resource not found',
     0, 'skip', 13);

-- ── recovery_taxonomy: 9 canonical rows ──────────────────────────────────────
-- rollback_depth: 0 = no rollback, 1 = current turn, -1 = full restart.

INSERT OR IGNORE INTO recovery_taxonomy
    (strategy_id, description, requires_state_preservation,
     typical_rollback_depth)
VALUES
    ('immediate_retry',   'Retry immediately without delay',
     0,  0),

    ('backoff_retry',     'Retry after exponential or fixed backoff',
     0,  0),

    ('fallback_tool',     'Switch to alternative tool',
     0,  0),

    ('skip',              'Skip failed step, continue execution',
     0,  0),

    ('abort',             'Abort goal execution',
     0, -1),

    ('full_restart',      'Restart entire goal from scratch',
     0, -1),

    ('turn_retry',        'Retry from current LLM turn',
     1,  1),

    ('tool_retry',        'Retry only the failed tool invocation',
     0,  0),

    ('checkpoint_resume', 'Resume from nearest checkpoint',
     1,  0);
