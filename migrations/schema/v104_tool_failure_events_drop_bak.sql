-- v104: Drop tool_failure_events_bak after successful data migration.
-- Part 4 of 4 (v101-v104).
-- At this point tool_failure_events contains all rows from bak (v103).
-- Dropping bak completes the schema correction sequence.
--
-- MSC-1: immutable after first commit.
-- MSC-4: DDL only.

DROP TABLE tool_failure_events_bak;
