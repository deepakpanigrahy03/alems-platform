-- v108_recovery_events.sql
-- DDL ONLY (MSC-4: no INSERT/UPDATE/DELETE in schema/ files)
-- Implements: B1.1 through B1.7
-- Depends on: failure_taxonomy, recovery_taxonomy (already exist, v082 equivalent)
-- Backward compat: existing runs have no recovery_events rows (B1.6)
-- DO NOT EDIT after first commit (MSC-1). Fix forward with v109+.

CREATE TABLE IF NOT EXISTS recovery_events (
    recovery_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id              INTEGER NOT NULL
                                REFERENCES goal_attempt(attempt_id),
    goal_id                 INTEGER NOT NULL
                                REFERENCES goal_execution(goal_id),
    -- NULL for single-agent runs; chunk 30 forward compat (P10)
    agent_id                INTEGER,
    failure_type_id         TEXT
                                REFERENCES failure_taxonomy(failure_type_id),
    recovery_strategy       TEXT
                                REFERENCES recovery_taxonomy(strategy_id),
    -- Measured in TURNS per P6:
    --  0  = tool-only retry within current turn (LLM context preserved)
    --  1  = retry current turn from its LLM call
    --  N  = retry from N turns back
    -- -1  = full goal restart
    rollback_depth_turns    INTEGER NOT NULL DEFAULT 0,
    -- Measured in STEPS (orchestration_events) per P6
    total_trajectory_steps  INTEGER
                                CHECK(total_trajectory_steps IS NULL
                                      OR total_trajectory_steps >= 0),
    replayed_steps          INTEGER
                                CHECK(replayed_steps IS NULL
                                      OR replayed_steps >= 0),
    -- replay_fraction = replayed_steps / total_trajectory_steps
    replay_fraction         REAL
                                CHECK(replay_fraction IS NULL
                                      OR (replay_fraction >= 0
                                          AND replay_fraction <= 1)),
    preserved_state_bytes   INTEGER
                                CHECK(preserved_state_bytes IS NULL
                                      OR preserved_state_bytes >= 0),
    recovery_point_step     INTEGER,
    recovery_point_phase    TEXT
                                CHECK(recovery_point_phase IS NULL
                                      OR recovery_point_phase IN
                                         ('planning','execution','synthesis')),
    -- Row created BEFORE recovery starts (recovery_start_ns populated first).
    -- recovery_end_ns and recovery_success backfilled AFTER recovery completes.
    -- Incomplete row (recovery_end_ns IS NULL) means process crashed mid-recovery.
    recovery_start_ns       INTEGER,
    recovery_end_ns         INTEGER,
    recovery_energy_uj      REAL
                                CHECK(recovery_energy_uj IS NULL
                                      OR recovery_energy_uj >= 0),
    -- 1 = succeeded after recovery, 0 = failed again, NULL = in progress
    recovery_success        INTEGER
                                CHECK(recovery_success IS NULL
                                      OR recovery_success IN (0, 1)),
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recovery_attempt
    ON recovery_events(attempt_id);

CREATE INDEX IF NOT EXISTS idx_recovery_goal
    ON recovery_events(goal_id);

CREATE INDEX IF NOT EXISTS idx_recovery_type
    ON recovery_events(failure_type_id);

CREATE INDEX IF NOT EXISTS idx_recovery_depth
    ON recovery_events(rollback_depth_turns);

-- Stephen's core result view.
-- Median not computed here: SQLite has no native MEDIAN.
-- Use scripts/analysis/recovery_depth_stats.py for median and std.
CREATE VIEW IF NOT EXISTS v_recovery_cost_by_depth AS
SELECT
    rollback_depth_turns,
    recovery_strategy,
    COUNT(*)                                                AS sample_count,
    AVG(recovery_energy_uj) / 1e6                          AS recovery_cost_j_mean,
    AVG(replay_fraction)                                   AS avg_replay_fraction,
    SUM(CASE WHEN recovery_success = 1 THEN 1 ELSE 0 END) AS success_count,
    ROUND(
        SUM(CASE WHEN recovery_success = 1 THEN 1 ELSE 0 END) * 100.0
        / NULLIF(COUNT(*), 0),
    2)                                                     AS success_rate_pct
FROM recovery_events
GROUP BY rollback_depth_turns, recovery_strategy
ORDER BY rollback_depth_turns;
