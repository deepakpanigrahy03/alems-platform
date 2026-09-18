-- Migration v095: Add attempt_id to orchestration_events
-- Fixes Bug 7: orchestration events have no per-attempt attribution.
-- Backward compatible: nullable column, existing rows get NULL.
-- MSC-4: DDL only. No INSERT here.
-- Prerequisite: none (additive ALTER TABLE only).

ALTER TABLE orchestration_events
  ADD COLUMN attempt_id INTEGER REFERENCES goal_attempt(attempt_id);

-- agent_id: P10 forward compat for chunk 30 multi-agent.
-- NULL for all single-agent runs.
ALTER TABLE orchestration_events
  ADD COLUMN agent_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_orch_events_attempt
  ON orchestration_events(attempt_id);
