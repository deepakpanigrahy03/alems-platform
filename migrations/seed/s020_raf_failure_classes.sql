-- s020: failure classes for contaminating and persistent failures (RAF R1).
-- Data only (MSC-4). Explicit column lists (MSC-6). Idempotent.
INSERT OR IGNORE INTO failure_taxonomy
    (failure_type_id, domain, description, default_retryable,
     default_recovery_strategy, typical_cost_rank)
VALUES
    ('silent_tool_output', 'execution',
     'Tool executed but returned a plausible wrong value without error', 1,
     'rollback_retry', NULL),
    ('wrong_model_step', 'reasoning',
     'Model issued a valid tool call with a plausible wrong argument', 1,
     'rollback_retry', NULL),
    ('silent_detected', 'validation',
     'End of turn state check found the state differs from ground truth', 1,
     'rollback_retry', NULL),
    ('persistent_rate_limit', 'communication',
     'Rate limit that recurs on the first call after recovery', 1,
     'backoff_retry', NULL),
    ('persistent_tool_error', 'execution',
     'Tool error that recurs on the next call of the same tool after recovery', 1,
     'immediate_retry', NULL);
