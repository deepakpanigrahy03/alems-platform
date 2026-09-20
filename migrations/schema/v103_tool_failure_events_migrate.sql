-- v103: Copy rows from tool_failure_events_bak into new clean table.
-- Part 3 of 4 (v101-v104).
-- Explicit column list required (MSC-6) — column order differs across machines
-- with different migration histories.
-- failure_type values that were CHECK-valid ('other', 'malformed_input', etc.)
-- are preserved as-is — no coercion. Historical data stays readable.
-- On fresh installs tool_failure_events_bak is empty — INSERT copies zero rows,
-- which is correct and harmless.
--
-- MSC-1: immutable after first commit.
-- MSC-4: DML only.

INSERT INTO tool_failure_events (
    failure_id,
    attempt_id,
    goal_id,
    orchestration_event_id,
    tool_name,
    failure_type,
    failure_phase,
    error_message,
    retry_attempted,
    retry_success,
    recovery_strategy,
    wasted_energy_uj,
    created_at
)
SELECT
    failure_id,
    attempt_id,
    goal_id,
    orchestration_event_id,
    tool_name,
    failure_type,
    failure_phase,
    error_message,
    retry_attempted,
    retry_success,
    recovery_strategy,
    wasted_energy_uj,
    created_at
FROM tool_failure_events_bak;
