"""
Derived value diff between two stores for a run set.

Used for the expected diff list of a correction (COMMON 8.3): A' (replayed,
as is) against B' (replayed, corrected) isolates the effect of the
correction from ETL drift. Profiles in derived_profiles.yaml classify each
table: key columns, ignored columns (with reason), compared = all others.

AUTHOR: Deepak Panigrahy
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import yaml

from core.validation.persistence import open_ro

PROFILES_PATH = os.path.join(os.path.dirname(__file__), "derived_profiles.yaml")


@dataclass
class ColumnDiff:
    """Change of one column over the run set."""

    table: str
    column: str
    rows_changed: int
    runs_changed: int
    max_abs: Optional[float]
    max_rel: Optional[float]
    null_flips: int


@dataclass
class RowCountDiff:
    """Rows present on one side only (by key)."""

    table: str
    only_a: int
    only_b: int


def load_profiles(path: str = PROFILES_PATH) -> Dict[str, dict]:
    """Parsed derived_profiles.yaml (tables section)."""
    with open(path, "r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("tables") or {}


def diff_stores(store_a: str, store_b: str, runs: Sequence[int],
                profiles: Optional[Dict[str, dict]] = None):
    """Compare every profiled table for the run set; returns (columns, rowcounts)."""
    profiles = profiles or load_profiles()
    con = open_ro(store_a)
    con.execute("ATTACH DATABASE ? AS b", ("file:%s?mode=ro" % store_b,))
    con.execute("CREATE TEMP TABLE sel(run_id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO sel VALUES (?)", [(int(r),) for r in runs])
    cols: List[ColumnDiff] = []
    counts: List[RowCountDiff] = []
    try:
        for table, prof in profiles.items():
            counts.append(_row_counts(con, table, prof))
            cols.extend(_column_diffs(con, table, prof))
    finally:
        con.close()
    return [c for c in cols if c.rows_changed], [c for c in counts if c.only_a or c.only_b]


def _join(table: str, prof: dict) -> str:
    """FROM clause joining main and b on the key, restricted to selected runs."""
    on = " AND ".join("a.%s IS b2.%s" % (k, k) for k in prof["key"])
    rc = prof.get("run_column", "run_id")
    return "FROM main.%s a JOIN b.%s b2 ON %s WHERE a.%s IN (SELECT run_id FROM sel)" % (table, table, on, rc)


def _row_counts(con, table: str, prof: dict) -> RowCountDiff:
    """Rows whose key exists on one side only."""
    on = " AND ".join("x.%s IS y.%s" % (k, k) for k in prof["key"])
    rc = prof.get("run_column", "run_id")
    side = ("SELECT COUNT(*) FROM {x}.{t} x WHERE x.{rc} IN (SELECT run_id FROM sel) "
            "AND NOT EXISTS (SELECT 1 FROM {y}.{t} y WHERE {on})")
    a = con.execute(side.format(x="main", y="b", t=table, on=on, rc=rc)).fetchone()[0]
    b = con.execute(side.format(x="b", y="main", t=table, on=on, rc=rc)).fetchone()[0]
    return RowCountDiff(table, a, b)


def _column_diffs(con, table: str, prof: dict) -> List[ColumnDiff]:
    """One ColumnDiff per compared column."""
    skip = set(prof["key"]) | set(prof.get("ignore") or {})
    names = [r[1] for r in con.execute("PRAGMA main.table_info(%s)" % table) if r[1] not in skip]
    return [_one_column(con, table, prof, c) for c in names]


def _one_column(con, table: str, prof: dict, col: str) -> ColumnDiff:
    """Counts and magnitude of change for one column."""
    sql = ("SELECT COUNT(*), COUNT(DISTINCT a.{rc}), "
           "MAX(ABS(CAST(a.{c} AS REAL) - CAST(b2.{c} AS REAL))), "
           "MAX(ABS(CAST(a.{c} AS REAL) - CAST(b2.{c} AS REAL)) / NULLIF(ABS(CAST(a.{c} AS REAL)), 0)), "
           "SUM((a.{c} IS NULL) <> (b2.{c} IS NULL)) "
           "{j} AND a.{c} IS NOT b2.{c}").format(c=col, rc=prof.get("run_column", "run_id"), j=_join(table, prof))
    n, runs, mabs, mrel, flips = con.execute(sql).fetchone()
    return ColumnDiff(table, col, n, runs, mabs, mrel, flips or 0)
