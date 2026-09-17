#!/usr/bin/env python3
"""
================================================================================
SEED QUALITY CONFIG — DEPRECATED, see migrations/seed/s011_seed_quality_config.sql
================================================================================
PURPOSE:
    This script used to hardcode the 17-row QUALITY_CONFIG list and insert
    it manually. As of SPEC 35J, that data now lives in a tracked seed
    file (migrations/seed/s011_seed_quality_config.sql), applied
    automatically and consistently across the fleet by
    `python3 scripts/tools/alems_migrate.py` — checksummed, tracked in
    migration_history, and visible to `--plan`/`--check`, none of which
    this standalone script ever was.

    Keeping the data in two places (here and in s011) risks silent drift.
    This script no longer inserts anything — it points you to the real
    source of truth and, optionally, verifies what's already seeded.

USAGE:
    python scripts/seed_quality_config.py --verify

AUTHOR: Deepak Panigrahy
================================================================================
"""

import argparse
import sqlite3
from pathlib import Path


def verify(db_path: str) -> None:
    """Print seeded rows for manual verification. Read-only."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT task_category, metric_type, judge_method, threshold, dual_judge "
            "FROM task_quality_config ORDER BY task_category"
        ).fetchall()
        print(f"\ntask_quality_config — {len(rows)} rows:")
        print(f"{'category':<20} {'metric':<12} {'judge':<22} {'thresh':<8} {'dual'}")
        print("-" * 72)
        for r in rows:
            print(f"{r[0]:<20} {r[1]:<12} {r[2]:<22} {r[3]:<8} {r[4]}")
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

    parser = argparse.ArgumentParser(
        description="DEPRECATED: seeding moved to migrations/seed/s011_seed_quality_config.sql"
    )
    parser.add_argument("--db-path", default=None, help="Path to experiments.db")
    parser.add_argument("--verify", action="store_true", help="Print current rows (read-only)")
    args = parser.parse_args()

    print(
        "This script no longer seeds data.\n"
        "Run 'python3 scripts/tools/alems_migrate.py' instead — it applies "
        "migrations/seed/s011_seed_quality_config.sql automatically, "
        "tracked and checksummed, on every machine.\n"
    )

    if args.verify:
        from scripts.tools.path_loader import get_alems_db_path
        db = str(Path(args.db_path or get_alems_db_path()).resolve())
        verify(db)
