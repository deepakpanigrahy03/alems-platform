#!/usr/bin/env python3
"""
scripts/validate_attempt_boundaries.py

Validates attempt-level energy boundary data integrity.
Run from project root: python3 scripts/validate_attempt_boundaries.py

Checks:
  V1: orchestration_events.attempt_id — no NULLs in recent runs
  V2: runs.post_task_energy_uj — no NULLs in recent runs
  V3: runs.task_duration_ns — no NULLs in recent runs
  V4: framework_overhead_energy_uj = pre + post (within 5%)
  V5: orchestration_events.attempt_id column exists (schema v095)
  V6: attempt_id FK integrity — no orphan references
  V7: orchestration events within attempt ns boundaries

Exit code 0 = all pass. Exit code 1 = failures found.
Run after every deployment and after backfill_attempt_attribution.py.

Author: A-LEMS platform
"""

import sqlite3
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path.cwd()))
from scripts.tools.path_loader import get_alems_db_path

CHECKS = [
    ("V1_bug7_attempt_id_null", """
        SELECT COUNT(*) FROM orchestration_events oe
        JOIN runs r ON oe.run_id = r.run_id
        WHERE r.run_id > (SELECT MAX(run_id) - 100 FROM runs)
          AND oe.attempt_id IS NULL
          AND EXISTS (
              SELECT 1 FROM goal_attempt ga WHERE ga.run_id = oe.run_id
          )
    """, 0),

    ("V2_bug8_post_task_null", """
        SELECT COUNT(*) FROM runs r
        JOIN experiments e ON r.exp_id = e.exp_id
        WHERE r.run_id > (SELECT MAX(run_id) - 100 FROM runs)
          AND r.post_task_energy_uj IS NULL
          AND (SELECT platform_class FROM environment_config
               WHERE key = 'platform_class' LIMIT 1) != 'apple_silicon'
    """, 0),

    ("V3_bug6_task_duration_null", """
        SELECT COUNT(*) FROM runs
        WHERE run_id > (SELECT MAX(run_id) - 100 FROM runs)
          AND task_duration_ns IS NULL
    """, 0),

    ("V4_bug8_overhead_mismatch", """
        SELECT COUNT(*) FROM runs
        WHERE run_id > (SELECT MAX(run_id) - 100 FROM runs)
          AND framework_overhead_energy_uj IS NOT NULL
          AND pre_task_energy_uj IS NOT NULL
          AND post_task_energy_uj IS NOT NULL
          AND ABS(framework_overhead_energy_uj - (pre_task_energy_uj + post_task_energy_uj))
              * 100.0 / NULLIF(framework_overhead_energy_uj, 0) > 5.0
    """, 0),

    ("V5_column_exists", """
        SELECT CASE WHEN COUNT(*) = 1 THEN 0 ELSE 1 END
        FROM pragma_table_info('orchestration_events')
        WHERE name = 'attempt_id'
    """, 0),

    ("V6_attempt_id_fk_integrity", """
        SELECT COUNT(*) FROM orchestration_events oe
        WHERE oe.attempt_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM goal_attempt ga WHERE ga.attempt_id = oe.attempt_id
          )
    """, 0),

    ("V7_event_outside_attempt_window", """
        SELECT COUNT(*) FROM orchestration_events oe
        JOIN goal_attempt ga ON oe.attempt_id = ga.attempt_id
        WHERE oe.attempt_id IS NOT NULL
          AND ga.started_at_ns IS NOT NULL
          AND ga.finished_at_ns IS NOT NULL
          AND (
              oe.start_time_ns < ga.started_at_ns
              OR oe.end_time_ns > ga.finished_at_ns
          )
          AND oe.run_id > (SELECT MAX(run_id) - 100 FROM runs)
    """, 0),
]


def main() -> None:
    db_path = get_alems_db_path()
    logger.info("DB: %s", db_path)
    conn = sqlite3.connect(db_path)
    failures = 0
    for name, sql, expected in CHECKS:
        result = conn.execute(sql).fetchone()[0]
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            failures += 1
        logger.info("%s | %s | got=%s expected=%s", status, name, result, expected)
    conn.close()
    if failures:
        logger.error("%d check(s) failed.", failures)
        sys.exit(1)
    else:
        logger.info("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
