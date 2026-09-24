# scripts/tools/audit/utils.py
# Shared utilities for the A-LEMS audit regression kit.
# SPEC_39_1 section 7.
#
# Design: read-only. Never writes to the live DB. Never replays ETL.
# Captures what is actually stored and diffs it.
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
OWNERSHIP_YAML = REPO_ROOT / "config" / "schema" / "table_ownership.yaml"


def _audit_dir() -> Path:
    """
    Resolve audit data directory to <project_root>/audit/.
    Project root is db.parent (experiments.db sits directly in project root
    until 39.2 moves it to data/). Falls back to REPO_ROOT/audit if
    path_loader is unavailable.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "tools"))
        from path_loader import get_alems_db_path
        return Path(get_alems_db_path()).parent / "audit"
    except Exception:
        return REPO_ROOT / "audit"


def _reports_dir() -> Path:
    """Resolve reports directory to <project_root>/reports/."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO_ROOT / "scripts" / "tools"))
        from path_loader import get_alems_db_path
        return Path(get_alems_db_path()).parent / "reports"
    except Exception:
        return REPO_ROOT / "reports"


AUDIT_DIR = _audit_dir()
REPORTS_DIR = _reports_dir()

# Float comparison tolerance.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_TOL = 1e-6


# ---------------------------------------------------------------------------
# Ownership map
# ---------------------------------------------------------------------------

def load_ownership() -> Dict[str, Dict]:
    """Load config/schema/table_ownership.yaml."""
    with open(OWNERSHIP_YAML) as fh:
        raw = yaml.safe_load(fh)
    result: Dict[str, Dict] = {}
    for section in ("tables", "views"):
        block = raw.get(section) or {}
        for name, attrs in block.items():
            result[name] = {
                "owner": attrs.get("owner", "core"),
                "kind": "view" if section == "views" else "table",
                "golden_class": attrs.get("golden_class", "derived"),
                "natural_key": attrs.get("natural_key") or [],
                "run_linkage": attrs.get("run_linkage") or None,
            }
    return result


# ---------------------------------------------------------------------------
# DB path helper
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    """Return the live DB path via path_loader."""
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "scripts" / "tools"))
    from path_loader import get_alems_db_path
    return Path(get_alems_db_path())


# ---------------------------------------------------------------------------
# Row linkage
# ---------------------------------------------------------------------------

def _resolve_rowids(conn, table: str, info: Dict,
                    ref_run_ids: List[int]) -> Optional[List[int]]:
    """
    Return rowids in `table` belonging to reference run_ids.
    Returns None when table has no run linkage (dump all rows).
    Returns [] when linkage exists but no matching rows found.

    Linkage forms:
      "run_id"               direct FK to runs.run_id
      "goal_attempt.goal_id" two-hop via intermediate table
    """
    import sqlite3
    linkage = info.get("run_linkage")
    if not linkage:
        return None

    placeholders = ",".join("?" * len(ref_run_ids))
    parts = linkage.split(".")

    if len(parts) == 1:
        col = parts[0]
        try:
            rows = conn.execute(
                f"SELECT rowid FROM {table} WHERE {col} IN ({placeholders})",
                ref_run_ids,
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    mid_table, fk_col = parts[0], parts[1]
    try:
        mid_rows = conn.execute(
            f"SELECT {fk_col} FROM {mid_table} WHERE run_id IN ({placeholders})",
            ref_run_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    if not mid_rows:
        return []

    mid_ids = [r[0] for r in mid_rows]
    mid_ph = ",".join("?" * len(mid_ids))
    try:
        rows = conn.execute(
            f"SELECT rowid FROM {table} WHERE {fk_col} IN ({mid_ph})",
            mid_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Canonical row dump
# ---------------------------------------------------------------------------

def dump_table(conn, table: str, info: Dict,
               ref_run_ids: List[int],
               exclude_cols: Optional[List[str]] = None) -> List[Dict]:
    """Read rows from `table` for reference run_ids. Returns sorted row dicts."""
    import sqlite3
    exclude = set(exclude_cols or [])

    try:
        col_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return []
    if not col_rows:
        return []

    all_cols = [r[1] for r in col_rows]
    select_cols = [c for c in all_cols if c not in exclude]
    if not select_cols:
        return []

    col_csv = ", ".join(select_cols)
    natural_key = info.get("natural_key") or []
    rowids = _resolve_rowids(conn, table, info, ref_run_ids)

    if rowids is None:
        try:
            rows = conn.execute(f"SELECT {col_csv} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            return []
    elif len(rowids) == 0:
        return []
    else:
        ph = ",".join("?" * len(rowids))
        try:
            rows = conn.execute(
                f"SELECT {col_csv} FROM {table} WHERE rowid IN ({ph})",
                rowids,
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    result = [dict(zip(select_cols, row)) for row in rows]

    if natural_key:
        result.sort(key=lambda r: tuple(r.get(k) for k in natural_key))
    elif result:
        result.sort(key=lambda r: list(r.values())[0])

    return result


def dump_all(conn, ownership: Dict[str, Dict], ref_run_ids: List[int],
             nondeterministic: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """Dump every table and view in ownership for the reference run_ids."""
    import sqlite3
    nondeterministic = nondeterministic or {}
    result: Dict[str, Any] = {}

    schema_rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()
    result["__schema__"] = [
        {"type": r[0], "name": r[1], "sql": r[2]} for r in schema_rows
    ]

    for name, info in sorted(ownership.items()):
        exclude = list(nondeterministic.get(name, []))
        result[name] = dump_table(conn, name, info, ref_run_ids, exclude)

    return result


# ---------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------

def table_hash(rows: List[Dict]) -> str:
    """SHA-256 of canonical JSON of rows."""
    serialized = json.dumps(rows, sort_keys=True, default=str).encode()
    return hashlib.sha256(serialized).hexdigest()


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

def _floats_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(fa) and math.isnan(fb):
        return True
    if math.isinf(fa) or math.isinf(fb):
        return fa == fb
    denom = max(abs(fa), abs(fb), 1e-300)
    return abs(fa - fb) / denom <= FLOAT_REL_TOL or abs(fa - fb) <= FLOAT_ABS_TOL


def diff_dumps(baseline: Dict[str, Any], current: Dict[str, Any],
               ownership: Dict[str, Dict]) -> Dict[str, List]:
    """
    Compare two dumps.
    Returns {"changed": [...], "unchanged": [...]}
    changed: list of diff records (kind, table, key, col, old, new)
    unchanged: list of table names that are identical
    """
    changed: List[Dict] = []
    unchanged: List[str] = []

    if baseline.get("__schema__") != current.get("__schema__"):
        changed.append({"kind": "schema_diff", "table": "__schema__",
                        "old": baseline.get("__schema__"),
                        "new": current.get("__schema__")})

    b_tables = {k for k in baseline if not k.startswith("__")}
    c_tables = {k for k in current if not k.startswith("__")}

    for t in sorted(b_tables - c_tables):
        changed.append({"kind": "missing_table", "table": t})
    for t in sorted(c_tables - b_tables):
        changed.append({"kind": "extra_table", "table": t})

    for table in sorted(b_tables & c_tables):
        b_rows: List[Dict] = baseline[table]
        c_rows: List[Dict] = current[table]
        info = ownership.get(table, {})
        natural_key = info.get("natural_key") or []
        table_diffs: List[Dict] = []

        if len(b_rows) != len(c_rows):
            table_diffs.append({"kind": "row_count", "table": table,
                                 "old": len(b_rows), "new": len(c_rows)})

        if natural_key:
            c_index = {tuple(r.get(k) for k in natural_key): r for r in c_rows}
            for b_row in b_rows:
                key = tuple(b_row.get(k) for k in natural_key)
                c_row = c_index.get(key)
                if c_row is None:
                    table_diffs.append({"kind": "missing_row", "table": table,
                                        "key": str(key)})
                    continue
                _compare_rows(table_diffs, table, str(key), b_row, c_row)
        else:
            for i, (b_row, c_row) in enumerate(zip(b_rows, c_rows)):
                _compare_rows(table_diffs, table, str(i), b_row, c_row)

        if table_diffs:
            changed.extend(table_diffs)
        else:
            unchanged.append(table)

    return {"changed": changed, "unchanged": unchanged}


def _compare_rows(diffs: List[Dict], table: str, key: str,
                  b_row: Dict, c_row: Dict) -> None:
    for col in sorted(set(b_row) | set(c_row)):
        bv = b_row.get(col)
        cv = c_row.get(col)
        if bv == cv:
            continue
        if isinstance(bv, (int, float)) or isinstance(cv, (int, float)):
            if _floats_equal(bv, cv):
                continue
        diffs.append({"kind": "cell", "table": table, "key": key,
                      "col": col, "old": bv, "new": cv})


# ---------------------------------------------------------------------------
# Coverage check
# ---------------------------------------------------------------------------

def check_coverage(conn, ownership: Dict) -> List[str]:
    """Return violations for tables/views in DB not in ownership map."""
    violations: List[str] = []
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()
    for kind, name in rows:
        if name.startswith("sqlite_"):
            continue
        if name not in ownership:
            violations.append(
                f"{kind} '{name}' exists in DB but not in table_ownership.yaml"
            )
    return violations
