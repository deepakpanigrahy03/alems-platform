"""
scripts/etl/failure_cost_profile_etl.py

Per-Failure-Type Cost Profiling ETL (A3, chunk 8.6).
Extension: ext-failure-profiling.

Computes recovery cost statistics and success rates per failure type for a
given experiment group, then UPSERTs into failure_cost_profile.

After this ETL is in place, profiling a new failure type requires:
  - one INSERT into failure_taxonomy
  - one experiment run with that type injected
  - one call to this ETL

Zero code changes.

Attribution paths (per SPEC Section 3, P5 enforcement):
  tool_failure_events:
    Path A (preferred): orchestration_event_id → orchestration_events.attributed_energy_uj
    Path B (fallback):  attempt_id → goal_attempt.energy_uj

  hallucination_events:
    Path A (preferred): wasted_energy_uj_real if non-NULL and > 0
    Path B:             orchestration_event_id → orchestration_events.attributed_energy_uj
    Path C (fallback):  attempt_id → goal_attempt.energy_uj

Fan-out prevention: all joins go through attempt_id (one-to-one with
tool_failure_events). Joining through run_id or goal_id without attempt_id
qualification is prohibited (causes fan-out on multi-attempt goals).

CLI usage:
  python scripts/etl/failure_cost_profile_etl.py --group <group_id>
  python scripts/etl/failure_cost_profile_etl.py --group <group_id> --db <path>
  python scripts/etl/failure_cost_profile_etl.py --group <group_id> --dry-run

Dependency: requires A1 (failure_taxonomy) and A2 (failure_injection_log,
tool_failure_events populated). Bug 13 must be closed before running.
"""

import argparse
import logging
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Optional

# Resolve project root so this script runs from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.tools.path_loader import get_alems_db_path

DEFAULT_DB = Path(get_alems_db_path())

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fan-out invariant check (SPEC Section 3.4)
# ---------------------------------------------------------------------------

def check_fanout_invariant(conn: sqlite3.Connection) -> bool:
    """
    Verify that joining tool_failure_events to goal_attempt through attempt_id
    produces no duplicate failure_id values.

    Returns True if invariant holds (0 rows with join_count > 1).
    Logs an error and returns False if fan-out is detected.
    """
    rows = conn.execute("""
        SELECT tfe.failure_id, COUNT(*) AS join_count
        FROM tool_failure_events tfe
        JOIN goal_attempt ga ON tfe.attempt_id = ga.attempt_id
        GROUP BY tfe.failure_id
        HAVING join_count > 1
    """).fetchall()

    if rows:
        logger.error(
            "Fan-out invariant violated: %d failure_id values have >1 join row. "
            "Aborting — cost profiles would be inflated. "
            "Affected failure_ids: %s",
            len(rows),
            [r[0] for r in rows[:10]],
        )
        return False

    logger.debug("Fan-out invariant OK: no duplicate failure_id values.")
    return True


# ---------------------------------------------------------------------------
# Hallucination prerequisite check (SPEC Section 3.2)
# ---------------------------------------------------------------------------

def check_hallucination_attribution(conn: sqlite3.Connection) -> bool:
    """
    Verify that at least one attribution path for hallucination_events produces
    non-zero energy values.

    Returns True if hallucination data is usable.
    Returns False and logs a warning if all paths are NULL/zero — hallucination
    types are excluded from cost profiles until 8.5C populates the data.
    """
    row = conn.execute("""
        SELECT COUNT(*) FROM hallucination_events
        WHERE (wasted_energy_uj_real IS NOT NULL AND wasted_energy_uj_real > 0)
           OR orchestration_event_id IS NOT NULL
    """).fetchone()

    if row[0] == 0:
        logger.warning(
            "hallucination_events has no attributable energy data; "
            "hallucination type excluded from cost profiles until "
            "8.5C populates wasted_energy_uj_real or orchestration_event_id."
        )
        return False

    logger.debug("Hallucination attribution check OK: %d attributable rows.", row[0])
    return True


# ---------------------------------------------------------------------------
# Fetch failure events with attributed energy (tool_failure_events)
# ---------------------------------------------------------------------------

def _fetch_tool_failure_rows(
    conn: sqlite3.Connection,
    experiment_group: str,
) -> list[dict]:
    """
    Fetch all tool failure events for the experiment group with energy via
    Path A (orchestration_event_id) preferred, Path B (attempt energy) fallback.

    Join chain (one-to-one through attempt_id, no fan-out):
      experiments.group_id
        → experiments.exp_id
        → runs.exp_id
        → goal_attempt.run_id
        → tool_failure_events.attempt_id

    Returns list of dicts: failure_id, failure_type, energy_uj, attempt_id,
    goal_id, attempt_number.
    """
    rows = conn.execute("""
        SELECT
            tfe.failure_id,
            tfe.failure_type,
            tfe.attempt_id,
            ga.goal_id,
            ga.attempt_number,
            -- Path A: event-level attributed energy (preferred).
            -- Path B: full attempt energy (fallback when orchestration_event_id NULL).
            COALESCE(oe.attributed_energy_uj, ga.energy_uj) AS energy_uj,
            -- Flag which path was used for provenance logging.
            CASE
                WHEN tfe.orchestration_event_id IS NOT NULL THEN 'path_a'
                ELSE 'path_b'
            END AS attribution_path
        FROM tool_failure_events tfe
        JOIN goal_attempt ga
            ON tfe.attempt_id = ga.attempt_id
        JOIN runs r
            ON ga.run_id = r.run_id
        JOIN experiments e
            ON r.exp_id = e.exp_id
        LEFT JOIN orchestration_events oe
            ON tfe.orchestration_event_id = oe.event_id
        WHERE e.group_id = ?
          AND ga.outcome IN ('failure', 'partial')
    """, (experiment_group,)).fetchall()

    cols = [
        "failure_id", "failure_type", "attempt_id", "goal_id",
        "attempt_number", "energy_uj", "attribution_path",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Fetch failure events with attributed energy (hallucination_events)
# ---------------------------------------------------------------------------

def _fetch_hallucination_rows(
    conn: sqlite3.Connection,
    experiment_group: str,
) -> list[dict]:
    """
    Fetch hallucination events with energy via Path A/B/C (SPEC Section 3.2).

    Path A: wasted_energy_uj_real (direct measured waste).
    Path B: orchestration_event_id → attributed_energy_uj.
    Path C: attempt_id → goal_attempt.energy_uj (full attempt cost).

    Uses hallucination_type as the failure_type_id for grouping.
    """
    rows = conn.execute("""
        SELECT
            he.hallucination_id                     AS failure_id,
            he.hallucination_type                   AS failure_type,
            he.attempt_id,
            ga.goal_id,
            ga.attempt_number,
            -- Path A → B → C priority chain.
            COALESCE(
                CASE WHEN he.wasted_energy_uj_real IS NOT NULL
                          AND he.wasted_energy_uj_real > 0
                     THEN he.wasted_energy_uj_real END,
                oe.attributed_energy_uj,
                ga.energy_uj
            )                                       AS energy_uj,
            CASE
                WHEN he.wasted_energy_uj_real IS NOT NULL
                     AND he.wasted_energy_uj_real > 0 THEN 'path_a'
                WHEN he.orchestration_event_id IS NOT NULL THEN 'path_b'
                ELSE 'path_c'
            END                                     AS attribution_path
        FROM hallucination_events he
        JOIN goal_attempt ga
            ON he.attempt_id = ga.attempt_id
        JOIN runs r
            ON ga.run_id = r.run_id
        JOIN experiments e
            ON r.exp_id = e.exp_id
        LEFT JOIN orchestration_events oe
            ON he.orchestration_event_id = oe.event_id
        WHERE e.group_id = ?
    """, (experiment_group,)).fetchall()

    cols = [
        "failure_id", "failure_type", "attempt_id", "goal_id",
        "attempt_number", "energy_uj", "attribution_path",
    ]
    return [dict(zip(cols, r)) for r in rows]


# ---------------------------------------------------------------------------
# Recovery success check (SPEC Section 4.1)
# ---------------------------------------------------------------------------

def _build_success_index(conn: sqlite3.Connection, goal_ids: list[int]) -> set[tuple]:
    """
    Build a set of (goal_id, attempt_number) pairs where the NEXT attempt
    after that attempt_number succeeded.

    Recovery success definition (SPEC 4.1):
      EXISTS (
        SELECT 1 FROM goal_attempt ga2
        WHERE ga2.goal_id = ga.goal_id
          AND ga2.attempt_number = ga.attempt_number + 1
          AND ga2.outcome = 'success'
      )
    """
    if not goal_ids:
        return set()

    placeholders = ",".join("?" * len(goal_ids))
    rows = conn.execute(f"""
        SELECT ga.goal_id, ga.attempt_number
        FROM goal_attempt ga
        WHERE ga.goal_id IN ({placeholders})
          AND EXISTS (
              SELECT 1 FROM goal_attempt ga2
              WHERE ga2.goal_id = ga.goal_id
                AND ga2.attempt_number = ga.attempt_number + 1
                AND ga2.outcome = 'success'
          )
    """, goal_ids).fetchall()

    return {(r[0], r[1]) for r in rows}


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------

def _aggregate_by_type(
    rows: list[dict],
    success_index: set[tuple],
) -> dict[str, dict]:
    """
    Group failure rows by failure_type and compute cost statistics.

    Returns dict keyed by failure_type with aggregated metrics.
    """
    # Group rows by failure_type.
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        ftype = row["failure_type"]
        by_type.setdefault(ftype, []).append(row)

    result = {}
    for ftype, frows in by_type.items():
        # Collect energy values — skip NULL (energy attribution missing).
        energies = [r["energy_uj"] for r in frows if r["energy_uj"] is not None]
        sample_count = len(frows)

        # Count how many of these failures had a successful next attempt.
        success_count = sum(
            1 for r in frows
            if (r["goal_id"], r["attempt_number"]) in success_index
        )
        success_rate = success_count / sample_count if sample_count > 0 else None

        # Distribution stats — None when no energy data available.
        if energies:
            mean_val    = statistics.mean(energies)
            median_val  = statistics.median(energies)
            std_val     = statistics.stdev(energies) if len(energies) > 1 else 0.0
            sorted_e    = sorted(energies)
            n           = len(sorted_e)
            # Simple percentile interpolation.
            p25_val = sorted_e[max(0, int(n * 0.25) - 1)]
            p75_val = sorted_e[min(n - 1, int(n * 0.75))]
        else:
            mean_val = median_val = std_val = p25_val = p75_val = None

        # cost_per_recovery_success = E[cost] / P[success] (SPEC Section 4.2).
        # NULL when success_rate is 0 — division by zero guard.
        if mean_val is not None and success_rate and success_rate > 0:
            cost_per_success = mean_val / success_rate
        else:
            cost_per_success = None

        # Log path distribution for provenance.
        path_counts = {}
        for r in frows:
            path_counts[r["attribution_path"]] = path_counts.get(r["attribution_path"], 0) + 1
        logger.debug(
            "failure_type=%s sample_count=%d success_rate=%s attribution=%s",
            ftype, sample_count, success_rate, path_counts,
        )

        result[ftype] = {
            "sample_count":             sample_count,
            "recovery_cost_uj_mean":    mean_val,
            "recovery_cost_uj_median":  median_val,
            "recovery_cost_uj_p25":     p25_val,
            "recovery_cost_uj_p75":     p75_val,
            "recovery_cost_uj_std":     std_val,
            "recovery_success_rate":    success_rate,
            "recovery_success_count":   success_count,
            "cost_per_recovery_success": cost_per_success,
        }

    return result


# ---------------------------------------------------------------------------
# Upsert into failure_cost_profile
# ---------------------------------------------------------------------------

def _upsert_profiles(
    conn: sqlite3.Connection,
    experiment_group: str,
    aggregated: dict[str, dict],
    dry_run: bool = False,
) -> int:
    """
    INSERT OR REPLACE aggregated profiles into failure_cost_profile.

    Returns number of rows upserted.
    """
    rows_written = 0
    for ftype, metrics in aggregated.items():
        if dry_run:
            logger.info(
                "[DRY RUN] Would upsert: failure_type=%s group=%s "
                "sample_count=%d mean_uj=%s success_rate=%s cost_per_success=%s",
                ftype,
                experiment_group,
                metrics["sample_count"],
                metrics["recovery_cost_uj_mean"],
                metrics["recovery_success_rate"],
                metrics["cost_per_recovery_success"],
            )
            rows_written += 1
            continue

        conn.execute("""
            INSERT OR REPLACE INTO failure_cost_profile (
                failure_type_id,
                experiment_group,
                sample_count,
                recovery_cost_uj_mean,
                recovery_cost_uj_median,
                recovery_cost_uj_p25,
                recovery_cost_uj_p75,
                recovery_cost_uj_std,
                recovery_success_rate,
                recovery_success_count,
                recovery_attempts_mean,
                recovery_latency_ms_mean,
                cost_per_recovery_success,
                computed_at
            ) VALUES (
                :failure_type_id,
                :experiment_group,
                :sample_count,
                :recovery_cost_uj_mean,
                :recovery_cost_uj_median,
                :recovery_cost_uj_p25,
                :recovery_cost_uj_p75,
                :recovery_cost_uj_std,
                :recovery_success_rate,
                :recovery_success_count,
                NULL,
                NULL,
                :cost_per_recovery_success,
                CURRENT_TIMESTAMP
            )
        """, {
            "failure_type_id":            ftype,
            "experiment_group":           experiment_group,
            "sample_count":               metrics["sample_count"],
            "recovery_cost_uj_mean":      metrics["recovery_cost_uj_mean"],
            "recovery_cost_uj_median":    metrics["recovery_cost_uj_median"],
            "recovery_cost_uj_p25":       metrics["recovery_cost_uj_p25"],
            "recovery_cost_uj_p75":       metrics["recovery_cost_uj_p75"],
            "recovery_cost_uj_std":       metrics["recovery_cost_uj_std"],
            "recovery_success_rate":      metrics["recovery_success_rate"],
            "recovery_success_count":     metrics["recovery_success_count"],
            "cost_per_recovery_success":  metrics["cost_per_recovery_success"],
        })
        rows_written += 1

    if not dry_run:
        conn.commit()

    return rows_written


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_failure_cost_profiles(
    experiment_group: str,
    db_path: Path = DEFAULT_DB,
    include_hallucinations: bool = True,
    dry_run: bool = False,
) -> int:
    """
    Compute and UPSERT failure cost profiles for the given experiment group.

    Algorithm (SPEC Section 4):
    1. Check fan-out invariant — abort if violated.
    2. Fetch tool_failure_events rows with energy via Path A or B.
    3. Optionally fetch hallucination_events rows (if attribution data exists).
    4. Group by failure_type.
    5. Compute mean/median/p25/p75/std of recovery energy.
    6. Compute recovery_success_rate via next-attempt-succeeded definition.
    7. Compute cost_per_recovery_success = mean / success_rate.
    8. UPSERT into failure_cost_profile.

    Args:
        experiment_group:     experiments.group_id to profile.
        db_path:              Path to A-LEMS SQLite database.
        include_hallucinations: Whether to include hallucination_events rows.
                              Set False to skip even when data is available.
        dry_run:              Log what would be written without touching the DB.

    Returns:
        Number of failure_cost_profile rows computed (0 on abort).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Step 1: Fan-out invariant check — mandatory before any aggregation.
        if not check_fanout_invariant(conn):
            return 0

        # Step 2: Tool failure events.
        tool_rows = _fetch_tool_failure_rows(conn, experiment_group)
        logger.info(
            "group=%s: fetched %d tool_failure_events rows.",
            experiment_group, len(tool_rows),
        )

        # Step 3: Hallucination events (conditional on attribution data existing).
        halluc_rows: list[dict] = []
        if include_hallucinations:
            if check_hallucination_attribution(conn):
                halluc_rows = _fetch_hallucination_rows(conn, experiment_group)
                logger.info(
                    "group=%s: fetched %d hallucination_events rows.",
                    experiment_group, len(halluc_rows),
                )
            # If check fails, warning already logged inside the check function.

        all_rows = tool_rows + halluc_rows

        if not all_rows:
            logger.warning(
                "group=%s: no failure events found. "
                "Verify that injection experiments ran and Bug 13 is closed.",
                experiment_group,
            )
            return 0

        # Step 4-7: Aggregate by failure_type.
        # Build success index once for all goal_ids in scope — one DB round trip.
        goal_ids = list({r["goal_id"] for r in all_rows})
        success_index = _build_success_index(conn, goal_ids)
        logger.debug(
            "group=%s: %d unique goals, %d have a successful next attempt.",
            experiment_group, len(goal_ids), len(success_index),
        )

        aggregated = _aggregate_by_type(all_rows, success_index)
        logger.info(
            "group=%s: computed profiles for %d failure types: %s",
            experiment_group,
            len(aggregated),
            sorted(aggregated.keys()),
        )

        # Step 8: Upsert.
        n = _upsert_profiles(conn, experiment_group, aggregated, dry_run=dry_run)
        logger.info(
            "group=%s: %s %d failure_cost_profile rows.",
            experiment_group,
            "Would write" if dry_run else "Upserted",
            n,
        )
        return n

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "A3 Cost Profiling ETL — compute per-failure-type cost profiles "
            "for an experiment group and upsert into failure_cost_profile."
        )
    )
    p.add_argument(
        "--group", required=True,
        help="experiments.group_id to profile (e.g. 'injection_v1').",
    )
    p.add_argument(
        "--db", default=None,
        help="Path to A-LEMS SQLite DB. Defaults to get_alems_db_path().",
    )
    p.add_argument(
        "--no-hallucinations", action="store_true",
        help="Skip hallucination_events even if attribution data is present.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Log what would be written without touching the database.",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging.",
    )
    return p


def main() -> None:
    args = _build_arg_parser().parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = Path(args.db) if args.db else DEFAULT_DB

    if not db_path.exists():
        logger.error("Database not found: %s", db_path)
        sys.exit(1)

    n = compute_failure_cost_profiles(
        experiment_group=args.group,
        db_path=db_path,
        include_hallucinations=not args.no_hallucinations,
        dry_run=args.dry_run,
    )

    if n == 0:
        logger.warning("No profiles computed. Check warnings above.")
        sys.exit(1)

    print(f"OK: {n} failure_cost_profile rows {'(dry run)' if args.dry_run else 'upserted'}.")


if __name__ == "__main__":
    main()
