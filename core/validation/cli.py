"""
CLI layer for store validation: alems validate persistence.

Maps typed results of core.validation.persistence to output and exit codes
(C-CLI: 0 success, 2 usage, 3 environment, 4 invariant failure). Nothing
below this module prints for the user or exits.

    alems validate persistence [--store DB] [--runs 1,2] [--against 3,4] [--json]
    alems validate persistence --repair [--yes]     duplicate repair (INV-D1)

AUTHOR: Deepak Panigrahy
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from typing import List, Optional

from core.validation import persistence as P
from core.validation import repair as R


def _ids(text: Optional[str]) -> Optional[List[int]]:
    """Comma separated run ids, or None when the option is absent."""
    return [int(x) for x in text.split(",") if x.strip()] if text else None


def _parser() -> argparse.ArgumentParser:
    """Argument parser; the store comes from the launcher, never a literal path."""
    p = argparse.ArgumentParser(prog="alems validate persistence",
                                description="INV-D1, INV-D2 and coverage of run linked tables")
    p.add_argument("--store", default=os.environ.get("ALEMS_STORE"), help="store path (launcher supplies it)")
    p.add_argument("--runs", help="scope INV-D1 to these runs; with --against, the new run set")
    p.add_argument("--against", help="old run set: report coverage of --runs against it")
    p.add_argument("--json", action="store_true", help="machine output, schema alems.validate.persistence/1")
    p.add_argument("--repair", action="store_true", help="remove identical duplicates (plan only without --yes)")
    p.add_argument("--yes", action="store_true", help="apply the repair after a backup next to the store")
    p.add_argument("--no-backup", action="store_true",
                   help="skip the store backup (test data only; needs --yes)")
    return p


def _backup(store: str) -> str:
    """Consistent copy with the SQLite backup API before any repair write."""
    path = "%s.pre_repair_%s.db" % (store, time.strftime("%Y%m%d_%H%M%S"))
    src, dst = sqlite3.connect(store), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return path


def _run_repair(a) -> int:
    """Plan, refuse on conflicts, confirm (exit 5 without --yes), back up, apply, revalidate."""
    plan = R.plan_dedupe(a.store)
    rows = [s for s in plan.summary() if s[1] or s[2]]
    for t, n, c, runs in rows:
        print("%-28s remove=%-7d conflicts=%-5d runs=%d" % (t, n, c, runs))
    if any(c < 0 for _t, _n, c, _r in rows):
        print("RESULT FAIL: declared key columns missing in this store (conflicts=-1); nothing changed", file=sys.stderr)
        return 3
    if any(c for _t, _n, c, _r in rows):
        print("RESULT FAIL: conflicts are key matches with other content; nothing changed", file=sys.stderr)
        return 4
    if not rows:
        print("RESULT OK: nothing to repair")
        return 0
    if not a.yes:
        print("plan only; rerun with --yes to back up and apply", file=sys.stderr)
        return 5
    if getattr(a, "no_backup", False):
        print("backup skipped (--no-backup): repair runs in one transaction; no undo after success")
    else:
        print("backup %s" % _backup(a.store))
    # Administrative repair on a raw connection: transitional exception until the
    # store writer exposes administrative transactions (G35, 39.5.5).
    con = sqlite3.connect(a.store)
    try:
        done = R.apply_plan(con, plan)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    for k, v in sorted(done.items()):
        print("applied %-40s %d" % (k, v))
    r = P.validate(a.store)
    _print_report(r)
    return 0 if r.ok else 4


def _print_report(r: P.PersistenceReport) -> None:
    """Human summary of a validation report."""
    print("store %s" % r.store)
    print("evaluated %d, by construction %d, not evaluable %d, absent %d" % (
        len(r.evaluated), len(r.by_construction), len(r.not_evaluable), len(r.absent)))
    for name, reason in sorted(r.not_evaluable.items()):
        print("  not evaluable %-28s %s" % (name, reason))
    for f in r.findings:
        print("%-10s %-28s %8d  %s" % (f.check, f.table, f.count, f.detail))
    print("RESULT %s" % ("OK" if r.ok else "FAIL"))


def _run_coverage(a) -> int:
    """Coverage mode: exit 4 if any table LOST rows or errored."""
    rows = P.coverage(a.store, _ids(a.against), _ids(a.runs))
    bad = [r for r in rows if r.status not in ("OK", "NEW")]
    if a.json:
        print(json.dumps({"schema": "alems.validate.coverage/1", "ok": not bad,
                          "tables": [r.__dict__ for r in rows]}, indent=2))
    else:
        for r in rows:
            print("%-36s %-8s old=%s new=%s" % (r.table, r.status, r.old, r.new))
        print("RESULT %s (%d tables, %d not ok)" % ("OK" if not bad else "FAIL", len(rows), len(bad)))
    return 4 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point; returns the exit code."""
    a = _parser().parse_args(argv)
    if a.against and not a.runs:
        print("--against needs --runs", file=sys.stderr)
        return 2
    try:
        if a.repair:
            return _run_repair(a)
        if a.against:
            return _run_coverage(a)
        r = P.validate(a.store, runs=_ids(a.runs))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, sqlite3.Error) as exc:
        # Malformed declarations or an unreadable store are environment errors.
        print("validation could not run: %s" % exc, file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(r.to_dict(), indent=2))
    else:
        _print_report(r)
    return 0 if r.ok else 4


if __name__ == "__main__":
    sys.exit(main())
