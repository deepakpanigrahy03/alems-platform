-- =============================================================================
-- v100_injection_log.sql
-- SPEC 8.6-A2: Create failure_injection_log table.
-- Core schema — injection log is run provenance, not derived analysis.
-- Every injection decision (injected, skipped, suppressed, not_reached)
-- is recorded here for Paper 8 reproducibility.
--
-- run_id is NULL at insert time — backfilled by ETL after runs row is
-- created (same pattern as wasted_energy_uj in tool_failure_events).
-- attempt_id is always known at flush time.
--
-- Persistence is buffered: ScenarioInjector accumulates decisions in
-- memory during the attempt, goal_execution_manager batch INSERTs here
-- after finish_attempt(). A process crash before flush loses that
-- attempt's audit rows — documented tradeoff (A2 design decision).
--
-- injected_type references failure_taxonomy — requires A1 (v098) applied.
-- MSC-4: DDL only. No data here.
-- Prerequisite: v098 (failure_taxonomy must exist for FK).
-- =============================================================================

CREATE TABLE IF NOT EXISTS failure_injection_log (
    injection_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- run_id: NULL at insert, backfilled by ETL after runs row is created.
    run_id                      INTEGER REFERENCES runs(run_id),
    attempt_id                  INTEGER REFERENCES goal_attempt(attempt_id),
    goal_id                     INTEGER REFERENCES goal_execution(goal_id),
    scenario_id                 TEXT NOT NULL,
    rule_index                  INTEGER NOT NULL,
    -- injected_type: FK to failure_taxonomy. NULL for non-injection decisions
    -- (eligible/skipped/target_not_reached) where no type was selected.
    injected_type               TEXT REFERENCES failure_taxonomy(failure_type_id),
    target_step                 INTEGER,
    target_phase                TEXT,
    target_tool                 TEXT,
    -- injection_time_ns: time.time_ns() at decision point inside should_inject().
    injection_time_ns           INTEGER,
    draw_number                 INTEGER NOT NULL DEFAULT 0,
    -- random_seed: SHA-256 hex prefix used for this draw. Empty string for
    -- deterministic modes (no randomness used).
    random_seed                 TEXT NOT NULL DEFAULT '',
    -- injection_algorithm_version: bumped when ScenarioInjector logic changes.
    -- Allows post-hoc identification of which algorithm produced each row.
    injection_algorithm_version TEXT NOT NULL DEFAULT 'v1',
    -- status: full decision lifecycle.
    -- eligible         = location matched, not yet drawn
    -- selected         = Bernoulli draw passed
    -- injected         = failure actually triggered (selected + max not exceeded)
    -- skipped          = Bernoulli draw failed (below rate threshold)
    -- target_not_reached = rule targeted a step that was never reached
    -- suppressed       = selected but max_injections already reached for this goal
    status                      TEXT NOT NULL CHECK(status IN (
                                    'eligible', 'selected', 'injected',
                                    'skipped', 'target_not_reached', 'suppressed'
                                )),
    skip_reason                 TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- idx_injection_log_attempt: primary join key — all queries start here.
CREATE INDEX IF NOT EXISTS idx_injection_log_attempt
    ON failure_injection_log(attempt_id);

CREATE INDEX IF NOT EXISTS idx_injection_log_run
    ON failure_injection_log(run_id);

CREATE INDEX IF NOT EXISTS idx_injection_log_scenario
    ON failure_injection_log(scenario_id);

CREATE INDEX IF NOT EXISTS idx_injection_log_type
    ON failure_injection_log(injected_type);

CREATE INDEX IF NOT EXISTS idx_injection_log_status
    ON failure_injection_log(status);
