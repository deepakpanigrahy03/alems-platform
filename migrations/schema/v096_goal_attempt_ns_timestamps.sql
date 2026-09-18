-- Migration v096: Add nanosecond timestamps to goal_attempt
-- Enables precise orchestration_events boundary validation.
-- Backward compatible: nullable, existing rows get NULL.
-- MSC-4: DDL only.

ALTER TABLE goal_attempt
  ADD COLUMN started_at_ns  INTEGER;  -- time.time_ns() at attempt start

ALTER TABLE goal_attempt
  ADD COLUMN finished_at_ns INTEGER;  -- time.time_ns() at attempt finish
