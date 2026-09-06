#!/usr/bin/env python3
"""
audit_table_coverage.py — walks every table and column in the A-LEMS
database, reports NULL/zero coverage stats, flags suspicious columns
(all-NULL, all-zero, all-identical-value) for manual follow-up.

This does NOT diagnose root causes — it only surfaces WHERE to look.
Each flagged column still needs the same manual code-tracing process
used for BUG-01/02/03/04/05 tonight before writing any fix.

Usage:
    python3 audit_table_coverage.py [--limit N] [--table TABLE_NAME]

    --limit N        Only look at the most recent N rows per table
                     (by rowid, descending). Default: all rows.
    --table NAME     Only audit one specific table (for follow-up
                     after a first full pass).
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Reuse the project's own DB path resolution — never hardcode.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
try:
    from scripts.tools.path_loader import get_alems_db_path
    DB_PATH = get_alems_db_path()
except Exception as e:
    print(f"Could not resolve DB path via path_loader: {e}")
    print("Falling back to data/experiments.db")
    DB_PATH = "data/experiments.db"

# Tables that are static config/registry data, not per-run measurement
# data — auditing these for "NULL coverage" doesn't make sense the same
# way (they're expected to be sparse or single-row by design). Skipped
# by default; pass --table to audit one specifically if needed.
SKIP_TABLES = {
    "analysis_domain_config", "analysis_view_config", "component_registry",
    "cooling_devices", "energy_domains", "energy_sources", "eval_criteria",
    "gpu_config", "hardware_config", "idle_baseline_domains",
    "idle_baselines", "measurement_method_registry", "measurement_methodology",
    "method_references", "metric_analysis_domains", "metric_display_registry",
    "outlier_detection_config", "page_configs", "page_metric_configs",
    "page_sections", "page_templates", "platform_domain_relationships",
    "power_limits", "power_rails", "query_registry", "retry_policy",
    "standardization_registry", "task_categories", "task_quality_config",
    "task_retry_override", "thermal_zones", "schema_version",
    "sqlite_sequence", "environment_config", "experiments",
    "machine_setup_history", "migration_history", "audit_log", "etl_queue",
}


def get_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()
    return [r[0] for r in rows]


def get_columns(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    # (cid, name, type, notnull, dflt_value, pk)
    return [(r[1], r[2]) for r in rows]


def audit_table(conn, table, limit=None):
    columns = get_columns(conn, table)
    if not columns:
        return None

    limit_clause = f" ORDER BY rowid DESC LIMIT {limit}" if limit else ""
    total_row = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM {table}{limit_clause})"
    ).fetchone()
    total = total_row[0] if total_row else 0

    if total == 0:
        return {"table": table, "total_rows": 0, "columns": [], "empty": True}

    results = []
    for col_name, col_type in columns:
        try:
            # NULL count
            null_count = conn.execute(
                f'SELECT COUNT(*) FROM (SELECT "{col_name}" FROM {table}{limit_clause}) '
                f'WHERE "{col_name}" IS NULL'
            ).fetchone()[0]

            # Zero count (only meaningful for numeric-looking columns,
            # but harmless to check on any column — TEXT '0' won't match
            # numeric 0 due to SQLite's type affinity rules for '=')
            zero_count = conn.execute(
                f'SELECT COUNT(*) FROM (SELECT "{col_name}" FROM {table}{limit_clause}) '
                f'WHERE "{col_name}" = 0'
            ).fetchone()[0]

            # Distinct non-null value count (capped check — just need to
            # know if it's 0, 1, or "many" distinct values)
            distinct_count = conn.execute(
                f'SELECT COUNT(DISTINCT "{col_name}") FROM '
                f'(SELECT "{col_name}" FROM {table}{limit_clause}) '
                f'WHERE "{col_name}" IS NOT NULL'
            ).fetchone()[0]

            null_pct = round(null_count / total * 100, 1)
            zero_pct = round(zero_count / total * 100, 1)

            flag = None
            if null_pct == 100.0:
                flag = "ALL_NULL"
            elif zero_pct == 100.0 and distinct_count <= 1:
                flag = "ALL_ZERO"
            elif distinct_count == 1 and null_pct < 100.0 and zero_pct < 100.0:
                flag = "ALL_SAME_VALUE"
            elif null_pct >= 90.0:
                flag = "MOSTLY_NULL"

            results.append({
                "column": col_name,
                "type": col_type,
                "null_pct": null_pct,
                "zero_pct": zero_pct,
                "distinct_count": distinct_count,
                "flag": flag,
            })
        except sqlite3.OperationalError as e:
            results.append({
                "column": col_name,
                "type": col_type,
                "error": str(e),
            })

    return {"table": table, "total_rows": total, "columns": results, "empty": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Only audit the most recent N rows per table")
    parser.add_argument("--table", type=str, default=None,
                         help="Only audit this one table")
    args = parser.parse_args()

    print(f"Database: {DB_PATH}")
    print(f"Row limit per table: {args.limit or 'ALL'}")
    print("=" * 100)

    conn = sqlite3.connect(DB_PATH)
    tables = [args.table] if args.table else get_tables(conn)

    flagged_summary = []

    for table in tables:
        if not args.table and table in SKIP_TABLES:
            continue

        result = audit_table(conn, table, args.limit)
        if result is None:
            continue

        if result["empty"]:
            print(f"\n### {table} — EMPTY TABLE (0 rows)")
            flagged_summary.append((table, None, "EMPTY_TABLE"))
            continue

        flagged_cols = [c for c in result["columns"] if c.get("flag")]
        error_cols = [c for c in result["columns"] if c.get("error")]

        if not flagged_cols and not error_cols:
            continue  # clean table, don't clutter output

        print(f"\n### {table} ({result['total_rows']} rows audited)")
        for c in error_cols:
            print(f"  ERROR   {c['column']:35s} {c['type']:15s} {c['error']}")
        for c in flagged_cols:
            print(
                f"  {c['flag']:15s} {c['column']:35s} {c['type']:15s} "
                f"null={c['null_pct']}% zero={c['zero_pct']}% distinct={c['distinct_count']}"
            )
            flagged_summary.append((table, c["column"], c["flag"]))

    conn.close()

    print("\n" + "=" * 100)
    print(f"SUMMARY: {len(flagged_summary)} flagged column(s) across "
          f"{len(set(t for t, _, _ in flagged_summary))} table(s)")
    print("=" * 100)
    by_flag = {}
    for table, col, flag in flagged_summary:
        by_flag.setdefault(flag, []).append(f"{table}.{col}" if col else table)
    for flag, items in sorted(by_flag.items()):
        print(f"\n{flag} ({len(items)}):")
        for item in items:
            print(f"  - {item}")


if __name__ == "__main__":
    main()
