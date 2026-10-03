-- v118: one attempt, one run (G137, DESIGN_39_5_1_G137_v3, decision D).
-- idx_runs_unique encoded "one run per repetition side"; retries now own a run
-- each, so the rule is false and is dropped. No replacement key on runs:
-- one save per window in code, INV-A1 and the persistence validator detect.
DROP INDEX IF EXISTS idx_runs_unique;

-- Runs with their goal meaning, without putting goal columns into runs (C2).
-- Runs without a goal (baseline, calibration) show NULL goal columns.
CREATE VIEW IF NOT EXISTS v_runs_goal AS
SELECT r.*,
       ga.goal_id,
       ga.attempt_id,
       ga.attempt_number,
       ga.outcome,
       ga.is_winning
FROM runs r
LEFT JOIN goal_attempt ga ON ga.run_id = r.run_id;

INSERT INTO schema_version (version, applied_at, description)
VALUES (118, datetime('now'), 'drop idx_runs_unique (one run per attempt, G137); view v_runs_goal');
