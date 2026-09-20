-- v102: Create tool_failure_events with clean schema — no domain CHECK constraints.
-- Part 2 of 4 (v101-v104).
-- failure_type, failure_phase, recovery_strategy are free text.
-- Vocabulary enforced at application layer against dimension tables.
--
-- MSC-1: immutable after first commit.
-- MSC-4: DDL only.

CREATE TABLE tool_failure_events (
    failure_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id              INTEGER NOT NULL,
    goal_id                 INTEGER NOT NULL,
    orchestration_event_id  INTEGER,
    tool_name               TEXT NOT NULL,
    failure_type            TEXT NOT NULL,
    failure_phase           TEXT,
    error_message           TEXT,
    retry_attempted         INTEGER NOT NULL DEFAULT 0,
    retry_success           INTEGER NOT NULL DEFAULT 0,
    recovery_strategy       TEXT,
    wasted_energy_uj        REAL,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attempt_id)             REFERENCES goal_attempt(attempt_id),
    FOREIGN KEY (goal_id)                REFERENCES goal_execution(goal_id),
    FOREIGN KEY (orchestration_event_id) REFERENCES orchestration_events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_fail_attempt ON tool_failure_events(attempt_id);
CREATE INDEX IF NOT EXISTS idx_tool_fail_goal    ON tool_failure_events(goal_id);
CREATE INDEX IF NOT EXISTS idx_tool_fail_tool    ON tool_failure_events(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_fail_type    ON tool_failure_events(failure_type);
