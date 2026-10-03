"""
core/integrity/catalog.py (39.5.1 WP 1e, contract C-INV)

Evaluates the invariant catalog (invariants.yaml) against one store, read only,
and returns a machine readable report for gates, CI and the integrity step.

Usage:
    python -m core.integrity.catalog --store <db> [--since RUN_ID] [--json] [--out FILE]

Exit codes (C-CLI): 0 pass, 3 environment error (store), 4 invariant failure.

AUTHOR: Deepak Panigrahy
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# The catalog ships beside this module so every engine carries its own version.
CATALOG_PATH = Path(__file__).with_name("invariants.yaml")
SAMPLE_LIMIT = 20  # rows kept per invariant; the count is always complete


def load_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load and minimally validate the catalog.

    Args:
        path: catalog file; defaults to the engine catalog.

    Returns:
        Parsed catalog dict.
    """
    data = yaml.safe_load((path or CATALOG_PATH).read_text())
    ids = [inv["id"] for inv in data["invariants"]]
    # Duplicate ids would make Leg C set differences ambiguous.
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate invariant ids in catalog")
    return data


def open_store_ro(store: Path) -> sqlite3.Connection:
    """
    Open a store read only. A missing file raises instead of SQLite silently
    creating an empty database (lesson of G97).
    """
    if not store.is_file():
        raise FileNotFoundError(f"store not found: {store}")
    return sqlite3.connect(f"file:{store}?mode=ro", uri=True)


def _eval_sql(conn: sqlite3.Connection, inv: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Run one SQL invariant; a query error is reported as not_evaluable, never as pass."""
    try:
        rows = conn.execute(inv["sql"], params).fetchall()
    except sqlite3.Error as exc:
        # Typical cause: an older store lacks a table or column the check needs.
        return {"status": "not_evaluable", "count": None, "sample": [], "reason": str(exc)}
    return {"status": None, "count": len(rows),
            "sample": [list(r) for r in rows[:SAMPLE_LIMIT]], "reason": None}


def _eval_all_zero(conn: sqlite3.Connection, inv: Dict[str, Any], since: int) -> Dict[str, Any]:
    """Flag columns that are exactly zero on every one of the last N runs."""
    window = int(inv.get("window", 10))
    hits: List[List[Any]] = []
    skipped: List[str] = []
    for col in inv["columns"]:
        try:
            vals = conn.execute(
                f"SELECT {col} FROM runs WHERE run_id > ? ORDER BY run_id DESC LIMIT ?",
                (since, window)).fetchall()
        except sqlite3.Error:
            skipped.append(col)  # column absent on this store
            continue
        # NULL means unavailable (INV-E1) and is not a zero; need a full window.
        if len(vals) == window and all(v[0] is not None and v[0] == 0 for v in vals):
            hits.append([f"column:{col}", f"0 on last {window} runs"])
    reason = ("columns absent: " + ", ".join(skipped)) if skipped else None
    return {"status": None, "count": len(hits), "sample": hits, "reason": reason}


def evaluate(conn: sqlite3.Connection, catalog: Dict[str, Any], since: int = 0) -> List[Dict[str, Any]]:
    """
    Evaluate every invariant.

    Args:
        conn: read only store connection.
        catalog: parsed catalog.
        since: only runs with run_id > since are checked.

    Returns:
        One result dict per invariant with status pass, fail, warn,
        not_evaluable or delegated.
    """
    params = {"since": since, "tol": int(catalog["tolerance_uj"]),
              "atol": int(catalog.get("alignment_tolerance_ns", 0))}
    results = []
    for inv in catalog["invariants"]:
        kind = inv["kind"]
        if kind == "sql":
            res = _eval_sql(conn, inv, params)
        elif kind == "all_zero":
            res = _eval_all_zero(conn, inv, since)
        else:
            # Delegated checks have their own command; listed so no id is silently missing.
            res = {"status": "delegated", "count": None, "sample": [],
                   "reason": "run: " + inv.get("command", "")}
        if res["status"] is None:
            res["status"] = "pass" if res["count"] == 0 else (
                "fail" if inv["severity"] == "fail" else "warn")
        res.update({"id": inv["id"], "severity": inv["severity"]})
        results.append(res)
    return results


def build_report(store: Path, since: int, catalog: Dict[str, Any],
                 results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble the report consumed by gates and the evidence bundle."""
    return {
        "catalog_version": catalog["version"],
        "tolerance_uj": catalog["tolerance_uj"],
        "store": str(store),
        "since_run_id": since,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failed": [r["id"] for r in results if r["status"] == "fail"],
        "results": results,
    }


def _print_human(report: Dict[str, Any]) -> None:
    """One line per invariant, then failing samples."""
    for r in report["results"]:
        count = "" if r["count"] is None else f" {r['count']}"
        reason = f"  ({r['reason']})" if r["reason"] else ""
        print(f"{r['status']:<14}{r['id']:<10}{count}{reason}")
        if r["status"] in ("fail", "warn"):
            for key, detail in r["sample"][:5]:
                print(f"              {key}  {detail}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry; returns a C-CLI exit code."""
    ap = argparse.ArgumentParser(prog="alems validate invariants")
    ap.add_argument("--store", default=None,
                    help="override; default is the store of the current sandbox")
    ap.add_argument("--since", type=int, default=0, help="check runs with run_id > SINCE")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path, help="write the JSON report to this file")
    args = ap.parse_args(argv)

    # The store comes from where the researcher stands (sandbox, design 7.7),
    # through the one resolver; a path is only an explicit override.
    from core.storage.resolver import resolve_store
    try:
        store = Path(resolve_store(args.store))
        conn = open_store_ro(store)
    except Exception as exc:  # resolver and SQLite errors both mean: environment
        print(f"error: {exc}", file=sys.stderr)
        return 3
    try:
        catalog = load_catalog()
        report = build_report(store, args.since, catalog,
                              evaluate(conn, catalog, args.since))
    finally:
        conn.close()

    if args.out:
        args.out.write_text(json.dumps(report, indent=2, default=str))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    return 4 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
