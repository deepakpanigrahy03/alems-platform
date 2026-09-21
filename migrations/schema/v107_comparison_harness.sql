-- Migration v107: A5 comparison harness schema
-- Chunk: 8.6-A5
-- Creates v_wasted_energy_per_success view and policy_comparison_results table.
-- Depends on: v105 (ear tables), v106 (policy seeds)

-- ============================================================
-- View: E_waste / N_success per experiment group
-- Headline metric for Paper 8 Section 4.
--
-- E_waste = SUM energy_uj across all non-winning attempts (is_winning=0).
-- N_success = COUNT distinct goals where success=1.
-- waste_per_success_uj = E_waste / N_success.
-- Higher = more energy wasted on failed attempts before reaching success.
-- ============================================================

CREATE VIEW IF NOT EXISTS v_wasted_energy_per_success AS
SELECT
    e.group_id                                          AS experiment_group,
    e.model_name,
    e.provider,
    SUM(CASE WHEN ga.is_winning = 0 THEN ga.energy_uj ELSE 0 END)
                                                        AS e_waste_uj,
    COUNT(DISTINCT CASE WHEN ge.success = 1
          THEN ge.goal_id END)                          AS n_success,
    CASE
        WHEN COUNT(DISTINCT CASE WHEN ge.success = 1
             THEN ge.goal_id END) > 0
        THEN SUM(CASE WHEN ga.is_winning = 0
                 THEN ga.energy_uj ELSE 0 END)
             * 1.0
             / COUNT(DISTINCT CASE WHEN ge.success = 1
               THEN ge.goal_id END)
        ELSE NULL
    END                                                 AS waste_per_success_uj,
    COUNT(DISTINCT ge.goal_id)                          AS total_goals,
    ROUND(
        COUNT(DISTINCT CASE WHEN ge.success = 1
              THEN ge.goal_id END) * 100.0
        / NULLIF(COUNT(DISTINCT ge.goal_id), 0),
    2)                                                  AS success_rate_pct,
    SUM(ga.energy_uj)                                   AS total_energy_uj,
    COUNT(ga.attempt_id)                                AS total_attempts
FROM experiments e
JOIN goal_execution ge ON ge.exp_id = e.exp_id
JOIN goal_attempt  ga  ON ga.goal_id = ge.goal_id
WHERE e.is_valid = 1
  AND e.experiment_type IN ('normal', 'retry_study', 'failure_injection')
GROUP BY e.group_id, e.model_name, e.provider;

-- ============================================================
-- Table: pairwise policy comparison results
-- Populated by policy_comparison_etl.compare_policies().
-- One row per (group_a, group_b) pair per comparison run.
-- ============================================================

CREATE TABLE IF NOT EXISTS policy_comparison_results (
    comparison_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_group_a      TEXT    NOT NULL,
    experiment_group_b      TEXT    NOT NULL,
    policy_a                TEXT    NOT NULL,
    policy_b                TEXT    NOT NULL,
    waste_per_success_a     REAL,
    waste_per_success_b     REAL,
    ratio                   REAL,
    -- ratio > 1: group_a wastes more energy per success than group_b
    -- ratio < 1: group_b wastes more energy per success than group_a
    success_rate_a          REAL    CHECK(success_rate_a IS NULL
                                      OR success_rate_a BETWEEN 0 AND 1),
    success_rate_b          REAL    CHECK(success_rate_b IS NULL
                                      OR success_rate_b BETWEEN 0 AND 1),
    total_energy_a          REAL,
    total_energy_b          REAL,
    sample_count_a          INTEGER CHECK(sample_count_a IS NULL
                                      OR sample_count_a >= 0),
    sample_count_b          INTEGER CHECK(sample_count_b IS NULL
                                      OR sample_count_b >= 0),
    computed_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pcr_groups
    ON policy_comparison_results(experiment_group_a, experiment_group_b);
