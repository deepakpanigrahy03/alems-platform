-- validate_b1_recovery.sql
-- Validation queries for B1 recovery_events implementation.
-- Run against the experiment DB after any recovery-enabled experiment.
-- All queries must return rows or empty set, never an error.
--
-- Usage:
--   DB=$(python3 -c "from scripts.tools.path_loader import get_alems_db_path; print(get_alems_db_path())")
--   sqlite3 "$DB" < scripts/validation/validate_b1_recovery.sql

-- ── 1. Table exists and has expected columns ─────────────────────────────────
SELECT 'CHECK 1: recovery_events table exists' AS check_name;
SELECT COUNT(*) AS column_count
FROM pragma_table_info('recovery_events');
-- Expected: 20 columns

-- ── 2. View exists ───────────────────────────────────────────────────────────
SELECT 'CHECK 2: v_recovery_cost_by_depth view exists' AS check_name;
SELECT COUNT(*) AS view_exists
FROM sqlite_master
WHERE type = 'view'
  AND name = 'v_recovery_cost_by_depth';
-- Expected: 1

-- ── 3. Indexes exist ─────────────────────────────────────────────────────────
SELECT 'CHECK 3: indexes exist' AS check_name;
SELECT name FROM sqlite_master
WHERE type = 'index'
  AND name IN (
      'idx_recovery_attempt',
      'idx_recovery_goal',
      'idx_recovery_type',
      'idx_recovery_depth'
  )
ORDER BY name;
-- Expected: 4 rows

-- ── 4. Referential integrity: all attempt_ids exist in goal_attempt ──────────
SELECT 'CHECK 4: referential integrity attempt_id' AS check_name;
SELECT COUNT(*) AS orphaned_attempts
FROM recovery_events re
LEFT JOIN goal_attempt ga ON re.attempt_id = ga.attempt_id
WHERE ga.attempt_id IS NULL;
-- Expected: 0

-- ── 5. Referential integrity: all goal_ids exist in goal_execution ───────────
SELECT 'CHECK 5: referential integrity goal_id' AS check_name;
SELECT COUNT(*) AS orphaned_goals
FROM recovery_events re
LEFT JOIN goal_execution ge ON re.goal_id = ge.goal_id
WHERE ge.goal_id IS NULL;
-- Expected: 0

-- ── 6. rollback_depth_turns valid range ──────────────────────────────────────
SELECT 'CHECK 6: rollback_depth_turns range' AS check_name;
SELECT COUNT(*) AS invalid_depth
FROM recovery_events
WHERE rollback_depth_turns < -1;
-- Expected: 0 (-1 = full restart is the minimum valid value)

-- ── 7. replay_fraction within 0-1 ────────────────────────────────────────────
SELECT 'CHECK 7: replay_fraction bounds' AS check_name;
SELECT COUNT(*) AS out_of_bounds
FROM recovery_events
WHERE replay_fraction IS NOT NULL
  AND (replay_fraction < 0 OR replay_fraction > 1);
-- Expected: 0

-- ── 8. No incomplete rows older than 10 minutes ──────────────────────────────
SELECT 'CHECK 8: no stale incomplete recovery rows' AS check_name;
SELECT COUNT(*) AS stale_incomplete
FROM recovery_events
WHERE recovery_end_ns IS NULL
  AND created_at < datetime('now', '-10 minutes');
-- Expected: 0 (incomplete rows mean process crashed mid-recovery)

-- ── 9. Core result view runs without error ───────────────────────────────────
SELECT 'CHECK 9: v_recovery_cost_by_depth runs cleanly' AS check_name;
SELECT
    rollback_depth_turns,
    recovery_strategy,
    sample_count,
    recovery_cost_j_mean,
    avg_replay_fraction,
    success_rate_pct
FROM v_recovery_cost_by_depth
LIMIT 20;
-- Expected: rows if any recovery events exist, empty set if none yet

-- ── 10. Recovery strategy FK valid ───────────────────────────────────────────
SELECT 'CHECK 10: recovery_strategy FK to recovery_taxonomy' AS check_name;
SELECT COUNT(*) AS unknown_strategies
FROM recovery_events re
LEFT JOIN recovery_taxonomy rt ON re.recovery_strategy = rt.strategy_id
WHERE re.recovery_strategy IS NOT NULL
  AND rt.strategy_id IS NULL;
-- Expected: 0

-- ── 11. Full restart rows have rollback_depth = -1 ───────────────────────────
SELECT 'CHECK 11: full_restart depth consistency' AS check_name;
SELECT COUNT(*) AS inconsistent_full_restart
FROM recovery_events
WHERE recovery_strategy = 'full_restart'
  AND rollback_depth_turns != -1;
-- Expected: 0

-- ── 12. Summary stats for Stephen ────────────────────────────────────────────
SELECT 'SUMMARY: recovery events by depth' AS check_name;
SELECT
    rollback_depth_turns,
    recovery_strategy,
    COUNT(*)                                    AS n,
    ROUND(AVG(replay_fraction), 3)              AS mean_replay_fraction,
    ROUND(AVG(recovery_energy_uj) / 1e6, 6)    AS mean_recovery_j,
    ROUND(
        SUM(CASE WHEN recovery_success = 1 THEN 1.0 ELSE 0 END)
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                           AS success_pct
FROM recovery_events
GROUP BY rollback_depth_turns, recovery_strategy
ORDER BY rollback_depth_turns;
