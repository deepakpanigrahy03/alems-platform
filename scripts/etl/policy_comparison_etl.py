"""
policy_comparison_etl.py
Chunk 8.6-A5: Wasted-Energy-Per-Success + Policy Comparison Harness

Compares two experiment groups on E_waste/N_success (the Paper 8 headline metric).
Reads v_wasted_energy_per_success, computes ratio, INSERTs into policy_comparison_results.

Usage:
    python scripts/etl/policy_comparison_etl.py \
        --group-a <group_id_a> \
        --group-b <group_id_b> \
        --policy-a <policy_name_a> \
        --policy-b <policy_name_b>

    # Compare all pairs from a study prefix:
    python scripts/etl/policy_comparison_etl.py \
        --study-prefix <prefix> \
        --policies flat_default ear_v1 ear_conservative ear_aggressive
"""

import argparse
import logging
import sqlite3
from itertools import combinations
from pathlib import Path

from scripts.tools.path_loader import get_alems_db_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core comparison function
# ---------------------------------------------------------------------------

def compare_policies(conn: sqlite3.Connection,
                     group_a: str,
                     group_b: str,
                     policy_a: str = None,
                     policy_b: str = None) -> dict:
    """
    Compare two experiment groups on E_waste/N_success.

    Reads v_wasted_energy_per_success for both groups.
    Computes ratio = waste_per_success_a / waste_per_success_b.
    If ratio > 1: group_a wastes more energy per success than group_b.
    If ratio < 1: group_b wastes more energy per success than group_a.

    INSERTs result into policy_comparison_results.
    Returns the comparison dict.

    Args:
        conn:     open SQLite connection
        group_a:  experiment_group identifier for policy A
        group_b:  experiment_group identifier for policy B
        policy_a: policy name label for group_a (looked up from experiments if None)
        policy_b: policy name label for group_b (looked up from experiments if None)

    Returns:
        dict with all comparison fields, or raises ValueError if groups not found.
    """
    row_a = _fetch_group_metrics(conn, group_a)
    row_b = _fetch_group_metrics(conn, group_b)

    if row_a is None:
        raise ValueError(f"No valid experiments found for group_a='{group_a}'")
    if row_b is None:
        raise ValueError(f"No valid experiments found for group_b='{group_b}'")

    wps_a = row_a["waste_per_success_uj"]
    wps_b = row_b["waste_per_success_uj"]

    ratio = None
    if wps_a is not None and wps_b is not None and wps_b != 0:
        ratio = wps_a / wps_b

    # Resolve policy names from experiments table if not provided
    if policy_a is None:
        policy_a = _lookup_policy_name(conn, group_a) or group_a
    if policy_b is None:
        policy_b = _lookup_policy_name(conn, group_b) or group_b

    result = {
        "experiment_group_a":  group_a,
        "experiment_group_b":  group_b,
        "policy_a":            policy_a,
        "policy_b":            policy_b,
        "waste_per_success_a": wps_a,
        "waste_per_success_b": wps_b,
        "ratio":               ratio,
        "success_rate_a":      _pct_to_frac(row_a["success_rate_pct"]),
        "success_rate_b":      _pct_to_frac(row_b["success_rate_pct"]),
        "total_energy_a":      row_a["total_energy_uj"],
        "total_energy_b":      row_b["total_energy_uj"],
        "sample_count_a":      row_a["total_goals"],
        "sample_count_b":      row_b["total_goals"],
    }

    conn.execute("""
        INSERT INTO policy_comparison_results (
            experiment_group_a, experiment_group_b,
            policy_a, policy_b,
            waste_per_success_a, waste_per_success_b,
            ratio,
            success_rate_a, success_rate_b,
            total_energy_a, total_energy_b,
            sample_count_a, sample_count_b
        ) VALUES (
            :experiment_group_a, :experiment_group_b,
            :policy_a, :policy_b,
            :waste_per_success_a, :waste_per_success_b,
            :ratio,
            :success_rate_a, :success_rate_b,
            :total_energy_a, :total_energy_b,
            :sample_count_a, :sample_count_b
        )
    """, result)
    conn.commit()

    _log_result(result)
    return result


# ---------------------------------------------------------------------------
# Study-level comparison: all pairs from a group prefix
# ---------------------------------------------------------------------------

def compare_study_policies(conn: sqlite3.Connection,
                           study_prefix: str,
                           policy_map: dict = None) -> list[dict]:
    """
    Compare all pairs of experiment groups sharing a common prefix.

    Args:
        conn:         open SQLite connection
        study_prefix: common prefix for group_id (e.g. 'paper8_v1')
        policy_map:   optional {group_id: policy_name} override

    Returns:
        list of comparison dicts, one per pair.
    """
    groups = _fetch_groups_by_prefix(conn, study_prefix)
    if len(groups) < 2:
        raise ValueError(
            f"Need at least 2 groups with prefix '{study_prefix}', "
            f"found {len(groups)}: {groups}"
        )

    results = []
    for group_a, group_b in combinations(groups, 2):
        policy_a = (policy_map or {}).get(group_a)
        policy_b = (policy_map or {}).get(group_b)
        result = compare_policies(conn, group_a, group_b, policy_a, policy_b)
        results.append(result)

    logger.info("compare_study_policies: %d pairs compared for prefix='%s'",
                len(results), study_prefix)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_group_metrics(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute("""
        SELECT
            experiment_group,
            waste_per_success_uj,
            success_rate_pct,
            total_energy_uj,
            total_goals,
            total_attempts
        FROM v_wasted_energy_per_success
        WHERE experiment_group = ?
    """, (group_id,)).fetchone()

    if row is None:
        return None
    return dict(zip(
        ["experiment_group", "waste_per_success_uj", "success_rate_pct",
         "total_energy_uj", "total_goals", "total_attempts"],
        row
    ))


def _lookup_policy_name(conn: sqlite3.Connection, group_id: str) -> str | None:
    row = conn.execute("""
        SELECT retry_policy_name
        FROM experiments
        WHERE group_id = ?
          AND retry_policy_name IS NOT NULL
        LIMIT 1
    """, (group_id,)).fetchone()
    return row[0] if row else None


def _fetch_groups_by_prefix(conn: sqlite3.Connection,
                             prefix: str) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT experiment_group
        FROM v_wasted_energy_per_success
        WHERE experiment_group LIKE ?
        ORDER BY experiment_group
    """, (f"{prefix}%",)).fetchall()
    return [r[0] for r in rows]


def _pct_to_frac(pct: float | None) -> float | None:
    if pct is None:
        return None
    return pct / 100.0


def _log_result(r: dict) -> None:
    ratio_str = f"{r['ratio']:.4f}" if r["ratio"] is not None else "N/A"
    winner = "A" if (r["ratio"] or 1.0) < 1.0 else "B"
    logger.info(
        "compare_policies: %s vs %s | policy_a=%s policy_b=%s | "
        "wps_a=%.0f wps_b=%.0f ratio=%s winner=%s",
        r["experiment_group_a"], r["experiment_group_b"],
        r["policy_a"], r["policy_b"],
        r["waste_per_success_a"] or 0,
        r["waste_per_success_b"] or 0,
        ratio_str, winner,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="A5 policy comparison ETL — compute E_waste/N_success ratio "
                    "between two experiment groups."
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--group-a", help="Experiment group ID for policy A")
    mode.add_argument("--study-prefix",
                      help="Common group_id prefix — compares all pairs")

    p.add_argument("--group-b",
                   help="Experiment group ID for policy B (required with --group-a)")
    p.add_argument("--policy-a", default=None,
                   help="Policy name label for group A (default: looked up from DB)")
    p.add_argument("--policy-b", default=None,
                   help="Policy name label for group B (default: looked up from DB)")
    p.add_argument("--policies", nargs="+",
                   help="Policy name suffixes for --study-prefix mode")
    p.add_argument("--db", default=None,
                   help="Path to experiments.db (default: get_alems_db_path())")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = Path(args.db) if args.db else Path(get_alems_db_path())
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)

    try:
        if args.group_a:
            if not args.group_b:
                raise ValueError("--group-b is required when using --group-a")
            result = compare_policies(
                conn, args.group_a, args.group_b,
                args.policy_a, args.policy_b
            )
            print(f"\nComparison result:")
            print(f"  {result['experiment_group_a']} ({result['policy_a']})")
            print(f"    waste_per_success: {result['waste_per_success_a']:.0f} uJ"
                  if result['waste_per_success_a'] else "    waste_per_success: N/A")
            print(f"    success_rate:      {result['success_rate_a']:.2%}"
                  if result['success_rate_a'] else "    success_rate: N/A")
            print(f"  {result['experiment_group_b']} ({result['policy_b']})")
            print(f"    waste_per_success: {result['waste_per_success_b']:.0f} uJ"
                  if result['waste_per_success_b'] else "    waste_per_success: N/A")
            print(f"    success_rate:      {result['success_rate_b']:.2%}"
                  if result['success_rate_b'] else "    success_rate: N/A")
            if result["ratio"] is not None:
                print(f"  ratio (A/B): {result['ratio']:.4f}")
                if result["ratio"] > 1:
                    print(f"  => {result['policy_b']} wastes less energy per success")
                elif result["ratio"] < 1:
                    print(f"  => {result['policy_a']} wastes less energy per success")
                else:
                    print(f"  => policies equivalent on this metric")

        else:
            results = compare_study_policies(conn, args.study_prefix)
            print(f"\n{len(results)} pairwise comparisons for prefix '{args.study_prefix}':")
            for r in results:
                ratio_str = f"{r['ratio']:.4f}" if r["ratio"] is not None else "N/A"
                print(f"  {r['policy_a']} vs {r['policy_b']}: ratio={ratio_str}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
