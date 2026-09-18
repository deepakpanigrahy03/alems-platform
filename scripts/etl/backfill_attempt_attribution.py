#!/usr/bin/env python3
"""
scripts/etl/backfill_attempt_attribution.py

Backfills attempt-level attribution data for all runs.
Safe to run multiple times — all operations are idempotent.
Run from project root: cd ~/mydrive/alems-platform && python3 scripts/etl/backfill_attempt_attribution.py

Fixes three gaps in historical data:

1. orchestration_events.attempt_id
   Events emitted before schema v095 have NULL attempt_id.
   Resolution: match by run_id to goal_attempt and UPDATE.

2. goal_attempt.started_at_ns / finished_at_ns
   Attempts created post-hoc (save_pair/save_single paths) have NULL or
   incorrect ns timestamps because they are inserted after the run ends.
   Resolution: backfill from runs.start_time_ns / runs.end_time_ns.

3. runs.post_task_energy_uj / pre_task_energy_uj
   Runs created before duration_fix_etl v2 path have NULL post/pre task energy.
   Resolution: rerun fix_run() which uses v2 sample stream where available.

Author: A-LEMS platform
"""

import sqlite3
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Run from project root so imports resolve correctly.
sys.path.insert(0, str(Path.cwd()))

from scripts.tools.path_loader import get_alems_db_path
from scripts.etl.duration_fix_etl import fix_run, fix_run_with_pretask

DB_PATH = Path(get_alems_db_path())


def backfill_attempt_id(conn: sqlite3.Connection) -> None:
    """
    Backfill attempt_id on orchestration_events where NULL.

    Matches events to goal_attempt by run_id.
    Skips runs with no goal_attempt row (linear-only runs have no events).
    """
    rows = conn.execute("""
        SELECT DISTINCT oe.run_id
        FROM orchestration_events oe
        WHERE oe.attempt_id IS NULL
        ORDER BY oe.run_id
    """).fetchall()
    logger.info("Fix 1 — attempt_id: %d runs with NULL attempt_id on events", len(rows))
    fixed = skipped = 0
    for (run_id,) in rows:
        attempt = conn.execute(
            "SELECT attempt_id FROM goal_attempt WHERE run_id = ? LIMIT 1",
            (run_id,)
        ).fetchone()
        if attempt:
            conn.execute(
                "UPDATE orchestration_events "
                "SET attempt_id = ? "
                "WHERE run_id = ? AND attempt_id IS NULL",
                (attempt[0], run_id)
            )
            fixed += 1
        else:
            skipped += 1
    conn.commit()
    logger.info("Fix 1 — attempt_id: fixed=%d skipped=%d (no goal_attempt row)", fixed, skipped)


def backfill_attempt_ns_timestamps(conn: sqlite3.Connection) -> None:
    """
    Backfill started_at_ns and finished_at_ns on goal_attempt from runs table.

    Targets attempts where ns timestamps are NULL or were set post-hoc
    (started_at_ns > run start, meaning it was set at insert time not run time).
    """
    rows = conn.execute("""
        SELECT ga.attempt_id, r.start_time_ns, r.end_time_ns
        FROM goal_attempt ga
        JOIN runs r ON r.run_id = ga.run_id
        WHERE ga.run_id > 0
          AND r.start_time_ns IS NOT NULL
          AND (
              ga.started_at_ns IS NULL
              OR ga.started_at_ns > r.start_time_ns + COALESCE(r.task_duration_ns, 0)
          )
        ORDER BY ga.attempt_id
    """).fetchall()
    logger.info("Fix 2 — ns timestamps: %d attempts to backfill", len(rows))
    fixed = 0
    for (attempt_id, start_ns, end_ns) in rows:
        conn.execute(
            "UPDATE goal_attempt "
            "SET started_at_ns = ?, finished_at_ns = ? "
            "WHERE attempt_id = ?",
            (start_ns, end_ns, attempt_id)
        )
        fixed += 1
    conn.commit()
    logger.info("Fix 2 — ns timestamps: fixed=%d", fixed)


def backfill_post_task_energy(conn: sqlite3.Connection) -> None:
    """
    Backfill post_task_energy_uj and pre_task_energy_uj for runs where NULL.

    Uses fix_run() from duration_fix_etl which uses the v2 sample stream
    on SPBM/Apple platforms and legacy energy_samples on x86 RAPL platforms.
    Idempotent: fix_run() preserves existing non-NULL values.
    """
    rows = conn.execute("""
        SELECT r.run_id, r.pre_task_duration_ns, r.post_task_duration_ns,
               r.cpu_fraction
        FROM runs r
        WHERE r.post_task_energy_uj IS NULL
        ORDER BY r.run_id
    """).fetchall()
    logger.info("Fix 3 — post_task_energy: %d runs with NULL post_task_energy_uj", len(rows))
    fixed = failed = 0
    for (run_id, pre_dur_ns, post_dur_ns, cpu_frac) in rows:
        # Use fix_run_with_pretask with None point reads — v2 path handles
        # platforms without point reads (Mac IOKit) via power extrapolation.
        pre_dur_sec  = (pre_dur_ns  or 100_000_000) / 1e9
        post_dur_sec = (post_dur_ns or 300_000_000) / 1e9
        cpu_frac     = cpu_frac or 0.05
        ok = fix_run_with_pretask(
            run_id,
            rapl_before_pretask=None,
            rapl_after_task=None,
            pre_task_duration_sec=pre_dur_sec,
            post_task_duration_sec=post_dur_sec,
            cpu_frac_pre=cpu_frac,
            cpu_frac_post=cpu_frac,
            db_path=DB_PATH,
        )
        if ok:
            fixed += 1
        else:
            failed += 1
            logger.warning("Fix 3 — fix_run failed for run_id=%d", run_id)
    logger.info("Fix 3 — post_task_energy: fixed=%d failed=%d", fixed, failed)


def main() -> None:
    logger.info("DB: %s", DB_PATH)
    if not DB_PATH.exists():
        logger.error("DB not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        backfill_attempt_id(conn)
        backfill_attempt_ns_timestamps(conn)
        backfill_post_task_energy(conn)
        logger.info("Backfill complete. Run validate_f1_f2.sql to verify.")
    except Exception as exc:
        logger.error("Backfill failed: %s", exc)
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
