-- s015_add_crashed_failure_type.sql
-- SPEC 8.6-A1 followup: 'crashed' found in goal_attempt.failure_type on GN100
-- (69 rows, outcome=failure, status=failed). Written by old goal_tracker.py
-- code that mapped outcome values to failure_type before taxonomy existed.
-- 'crashed' = process or harness crash during execution — distinct from
-- timeout (exceeded time limit) and tool_error (tool invocation failed).
-- Added to execution domain. default_retryable=0 — crash state is unknown.
-- MSC-4: data only. No DDL.
INSERT OR IGNORE INTO failure_taxonomy
    (failure_type_id, domain, description, default_retryable,
     default_recovery_strategy, typical_cost_rank)
VALUES
    ('crashed', 'execution',
     'Process or harness crashed during goal execution — terminal state unknown',
     0, 'abort', 14);
