"""
core/retry/ear_calibrator.py

EAR calibration loader (A4, chunk 8.6).

Reads failure_cost_profile (built by A3 ETL) for a given experiment group
and populates ear_policy_rules with heuristic starting thresholds.

Calibration heuristics (EAR v1 — Paper 8 will tune via ablation):
  max_attempts              = ceil(1.0 / recovery_success_rate)
                              Expected attempts needed to get one success.
  cost_threshold_uj         = recovery_cost_uj_mean * 2.0
                              Budget must cover at least 2x mean recovery cost.
  success_probability_threshold = 0.10
                              Retry only if >10% chance of success.
  calibrated_success_prob   = recovery_success_rate from failure_cost_profile.
  action                    = 'retry' when retryable per taxonomy,
                              'abort' for non-retryable types.

Scope note (A4.8): EAR v1 uses experiment-group-level calibration aggregated
across task categories and models. Conditional calibration (per-model,
per-task, per-provider) is stated as future work in Paper 8.

CLI usage:
  python core/retry/ear_calibrator.py --group <group_id> --policy ear_v1
  python core/retry/ear_calibrator.py --group <group_id> --policy ear_v1 --dry-run
"""

import argparse
import logging
import math
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.tools.path_loader import get_alems_db_path

DEFAULT_DB = Path(get_alems_db_path())

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Non-retryable types — calibrator sets action='abort' regardless of profiles.
# Mirrors retry_coordinator.is_retryable() mapping for consistency.
# ---------------------------------------------------------------------------
NON_RETRYABLE_TYPES = frozenset({
    "not_found",
    "auth_error",
    "capability_error",
    "context_overflow",
    "crashed",
})

# Minimum success probability threshold for EAR v1.
DEFAULT_SUCCESS_PROB_THRESHOLD = 0.10

# Budget multiplier — cost_threshold = mean_cost * this factor.
DEFAULT_BUDGET_MULTIPLIER = 2.0


# ---------------------------------------------------------------------------
# Policy creation / lookup
# ---------------------------------------------------------------------------

def _ensure_ear_policy(conn: sqlite3.Connection, policy_name: str) -> int:
    """
    Get or create an ear_policy row for the given policy_name.
    Returns ear_policy_id.
    """
    row = conn.execute(
        "SELECT ear_policy_id FROM ear_policy WHERE policy_name = ?",
        (policy_name,),
    ).fetchone()

    if row:
        logger.debug("ear_policy %r already exists (id=%d).", policy_name, row[0])
        return row[0]

    conn.execute(
        """
        INSERT INTO ear_policy (policy_name, description, calibration_scope)
        VALUES (?, ?, 'experiment_group')
        """,
        (policy_name, f"EAR v1 policy calibrated from experiment group data."),
    )
    conn.commit()
    ear_policy_id = conn.execute(
        "SELECT ear_policy_id FROM ear_policy WHERE policy_name = ?",
        (policy_name,),
    ).fetchone()[0]
    logger.info("Created ear_policy %r (id=%d).", policy_name, ear_policy_id)
    return ear_policy_id


# ---------------------------------------------------------------------------
# Core calibration
# ---------------------------------------------------------------------------

def calibrate_ear_from_profiles(
    conn: sqlite3.Connection,
    ear_policy_id: int,
    experiment_group: str,
    success_prob_threshold: float = DEFAULT_SUCCESS_PROB_THRESHOLD,
    budget_multiplier: float = DEFAULT_BUDGET_MULTIPLIER,
    dry_run: bool = False,
) -> int:
    """
    Read failure_cost_profile for experiment_group and populate ear_policy_rules.

    Args:
        conn:                   SQLite connection.
        ear_policy_id:          Target ear_policy row.
        experiment_group:       experiments.group_id used for A3 profiling.
        success_prob_threshold: Minimum success rate to allow retry (default 0.10).
        budget_multiplier:      cost_threshold = mean_cost * multiplier (default 2.0).
        dry_run:                Log without writing.

    Returns:
        Number of rules upserted.
    """
    profiles = conn.execute(
        """
        SELECT failure_type_id,
               sample_count,
               recovery_cost_uj_mean,
               recovery_success_rate
        FROM failure_cost_profile
        WHERE experiment_group = ?
          AND sample_count > 0
        """,
        (experiment_group,),
    ).fetchall()

    if not profiles:
        logger.warning(
            "No failure_cost_profile rows found for group=%s. "
            "Run failure_cost_profile_etl.py first.",
            experiment_group,
        )
        return 0

    rules_written = 0
    for ftype, sample_count, mean_cost_uj, success_rate in profiles:
        # Determine action — non-retryable types always abort.
        action = "abort" if ftype in NON_RETRYABLE_TYPES else "retry"

        # max_attempts: ceil(1 / success_rate) — expected draws to get one success.
        # Capped at 5 to prevent runaway retries on low-success types.
        if success_rate and success_rate > 0:
            max_attempts = min(5, math.ceil(1.0 / success_rate))
        else:
            # No successful recoveries observed — allow 1 attempt, then abort.
            max_attempts = 1

        # cost_threshold_uj: budget must cover at least budget_multiplier x mean cost.
        cost_threshold_uj = (
            mean_cost_uj * budget_multiplier if mean_cost_uj is not None else None
        )

        if dry_run:
            logger.info(
                "[DRY RUN] Would upsert rule: failure_type=%s action=%s "
                "max_attempts=%d cost_threshold_uj=%s calibrated_success_prob=%s "
                "success_prob_threshold=%s (sample_count=%d)",
                ftype, action, max_attempts, cost_threshold_uj,
                success_rate, success_prob_threshold, sample_count,
            )
            rules_written += 1
            continue

        conn.execute(
            """
            INSERT INTO ear_policy_rules (
                ear_policy_id,
                failure_type_id,
                max_attempts,
                cost_threshold_uj,
                calibrated_success_prob,
                success_probability_threshold,
                action,
                priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(ear_policy_id, failure_type_id) DO UPDATE SET
                max_attempts                    = excluded.max_attempts,
                cost_threshold_uj               = excluded.cost_threshold_uj,
                calibrated_success_prob         = excluded.calibrated_success_prob,
                success_probability_threshold   = excluded.success_probability_threshold,
                action                          = excluded.action
            """,
            (
                ear_policy_id,
                ftype,
                max_attempts,
                cost_threshold_uj,
                success_rate,
                success_prob_threshold,
                action,
            ),
        )
        logger.info(
            "Upserted rule: failure_type=%s action=%s max_attempts=%d "
            "cost_threshold_j=%.4f calibrated_success_prob=%s (n=%d)",
            ftype, action, max_attempts,
            (cost_threshold_uj / 1e6) if cost_threshold_uj else 0,
            success_rate, sample_count,
        )
        rules_written += 1

    if not dry_run:
        conn.commit()

    return rules_written


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def calibrate(
    experiment_group: str,
    policy_name: str,
    db_path: Path = DEFAULT_DB,
    success_prob_threshold: float = DEFAULT_SUCCESS_PROB_THRESHOLD,
    budget_multiplier: float = DEFAULT_BUDGET_MULTIPLIER,
    dry_run: bool = False,
) -> int:
    """
    Full calibration pipeline: ensure policy exists, then populate rules.

    Args:
        experiment_group:       experiments.group_id from A3 profiling run.
        policy_name:            Name for the ear_policy row (e.g. 'ear_v1').
        db_path:                Path to A-LEMS SQLite DB.
        success_prob_threshold: Minimum success probability to allow retry.
        budget_multiplier:      cost_threshold = mean_cost * multiplier.
        dry_run:                Log without writing.

    Returns:
        Number of ear_policy_rules rows upserted.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        ear_policy_id = _ensure_ear_policy(conn, policy_name)
        n = calibrate_ear_from_profiles(
            conn=conn,
            ear_policy_id=ear_policy_id,
            experiment_group=experiment_group,
            success_prob_threshold=success_prob_threshold,
            budget_multiplier=budget_multiplier,
            dry_run=dry_run,
        )
        logger.info(
            "Calibration complete: policy=%r group=%s rules=%d",
            policy_name, experiment_group, n,
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
            "A4 EAR Calibrator — populate ear_policy_rules from failure_cost_profile."
        )
    )
    p.add_argument("--group", required=True,
                   help="experiments.group_id used for A3 cost profiling.")
    p.add_argument("--policy", required=True,
                   help="Policy name to create/update in ear_policy (e.g. 'ear_v1').")
    p.add_argument("--db", default=None,
                   help="Path to A-LEMS DB. Defaults to get_alems_db_path().")
    p.add_argument("--success-threshold", type=float,
                   default=DEFAULT_SUCCESS_PROB_THRESHOLD,
                   help=f"Minimum success probability to allow retry (default {DEFAULT_SUCCESS_PROB_THRESHOLD}).")
    p.add_argument("--budget-multiplier", type=float,
                   default=DEFAULT_BUDGET_MULTIPLIER,
                   help=f"cost_threshold = mean_cost * multiplier (default {DEFAULT_BUDGET_MULTIPLIER}).")
    p.add_argument("--dry-run", action="store_true",
                   help="Log what would be written without touching the DB.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable DEBUG logging.")
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

    n = calibrate(
        experiment_group=args.group,
        policy_name=args.policy,
        db_path=db_path,
        success_prob_threshold=args.success_threshold,
        budget_multiplier=args.budget_multiplier,
        dry_run=args.dry_run,
    )

    if n == 0:
        logger.warning("No rules calibrated. Check warnings above.")
        sys.exit(1)

    print(f"OK: {n} ear_policy_rules rows {'(dry run)' if args.dry_run else 'upserted'} "
          f"for policy={args.policy!r}.")


if __name__ == "__main__":
    main()
