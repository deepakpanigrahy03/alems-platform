-- v101: Rename tool_failure_events to _bak before schema correction.
-- Part 1 of 4 (v101-v104): remove hardcoded domain CHECK constraints from fact table.
-- Domain vocabulary (failure_type, failure_phase, recovery_strategy) belongs in
-- dimension tables (failure_taxonomy, recovery_taxonomy), not DB CHECK constraints.
-- Application layer (tool_failure_recorder.py) enforces vocabulary at runtime.
--
-- Safe on fresh installs: schema.py creates tool_failure_events with clean schema
-- (IF NOT EXISTS), so this rename runs on an empty clean table — harmless.
-- Safe on existing machines: renames old constrained table before v102 recreates it.
--
-- MSC-1: immutable after first commit.
-- MSC-4: DDL only.

ALTER TABLE tool_failure_events RENAME TO tool_failure_events_bak;
