"""
core/validation/corrections.py (39.5.1 1d.3)

Registered data corrections for stored rows: each correction is declared once
(id, rule, columns) and applied the same way on every store and every box.
Dry run by default; --apply takes a backup next to the store first. Applying
twice changes nothing the second time (idempotent by construction).

Usage:
    python -m core.validation.corrections --store <path>            # plan only
    python -m core.validation.corrections --store <path> --apply    # backup, apply

Run only while no experiment is writing to the store (offline maintenance,
same rule as alems validate persistence --repair).
"""

import argparse
import logging
import sqlite3
import sys
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# G90: hardware stats that aggregate_run_stats stored as 0.0 when unavailable.
# Zero is physically impossible for each of them on a running machine
# (0 MHz clock, 0 degrees Celsius package, 0 interrupts per second), so a stored
# 0 can only mean "not measured" and becomes NULL (INV-E1, MIC-1).
G90_COLUMNS = ("cpu_busy_mhz", "cpu_avg_mhz", "package_temp_celsius",
               "max_temp_c", "min_temp_c", "interrupt_rate")


def _columns(conn, table):
    # type: (sqlite3.Connection, str) -> List[str]
    """Column names of a table; empty when the table is absent (older stores)."""
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def plan_g90(conn):
    # type: (sqlite3.Connection) -> Dict[str, int]
    """Rows per column that G90 would set to NULL."""
    present = set(_columns(conn, "runs"))
    return {c: conn.execute("SELECT COUNT(*) FROM runs WHERE %s = 0" % c).fetchone()[0]
            for c in G90_COLUMNS if c in present}


def apply_g90(conn):
    # type: (sqlite3.Connection) -> int
    """Set impossible zeros to NULL; returns rows changed."""
    changed = 0
    for col in plan_g90(conn):
        changed += conn.execute("UPDATE runs SET %s = NULL WHERE %s = 0" % (col, col)).rowcount
    return changed


def _prefill_rows(conn):
    """Interactions whose prefill must be (re)computed or cleared."""
    if "prefill_energy_uj" not in _columns(conn, "llm_interactions"):
        return []
    return conn.execute("""
        SELECT interaction_id, run_id, request_start_ns, first_token_time_ns, prefill_energy_uj
        FROM llm_interactions
        WHERE prefill_energy_uj IS NOT NULL
           OR (streaming_enabled = 1 AND first_token_time_ns IS NOT NULL)
    """).fetchall()


def _new_prefill(conn, row, platform_class):
    # type: (sqlite3.Connection, tuple, Optional[str]) -> Optional[int]
    """G119 rule: energy of [request_start, first_token] or None."""
    from core.attribution.energy_window import window_energy_for_run
    _, run_id, start_ns, token_ns, _old = row
    return window_energy_for_run(conn.cursor(), run_id, start_ns, token_ns,
                                 platform_class=platform_class).energy_uj


def plan_g119(conn, platform_class=None):
    # type: (sqlite3.Connection, Optional[str]) -> Dict[str, int]
    """Counts of prefill values that would change, by kind of change."""
    out = {"to_value": 0, "to_null": 0, "unchanged": 0}
    for row in _prefill_rows(conn):
        new = _new_prefill(conn, row, platform_class)
        if new == row[4]:
            out["unchanged"] += 1
        elif new is None:
            out["to_null"] += 1
        else:
            out["to_value"] += 1
    return out


def apply_g119(conn, platform_class=None):
    # type: (sqlite3.Connection, Optional[str]) -> int
    """Recompute every prefill with the window rule; returns rows changed."""
    changed = 0
    for row in _prefill_rows(conn):
        new = _new_prefill(conn, row, platform_class)
        if new != row[4]:
            conn.execute("UPDATE llm_interactions SET prefill_energy_uj = ? "
                         "WHERE interaction_id = ?", (new, row[0]))
            changed += 1
    return changed


CORRECTIONS = {
    "G90": (plan_g90, apply_g90),
    "G119": (plan_g119, apply_g119),
}


def backup(store):
    # type: (str) -> str
    """Consistent copy next to the store via the SQLite backup API."""
    target = "%s.pre_corrections_%d.bak" % (store, int(time.time()))
    src = sqlite3.connect(store)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return target


def run(store, only, apply_changes, platform_class=None):
    # type: (str, List[str], bool, Optional[str]) -> int
    """Plan (and optionally apply) the selected corrections; returns exit code."""
    conn = sqlite3.connect(store)
    try:
        for cid in only:
            plan_fn = CORRECTIONS[cid][0]
            args = (conn, platform_class) if cid == "G119" else (conn,)
            print("%s plan: %s" % (cid, plan_fn(*args)))
        if not apply_changes:
            print("dry run: nothing written (use --apply)")
            return 0
        print("backup: %s" % backup(store))
        with conn:  # one transaction for all selected corrections
            for cid in only:
                apply_fn = CORRECTIONS[cid][1]
                args = (conn, platform_class) if cid == "G119" else (conn,)
                print("%s applied: %d rows" % (cid, apply_fn(*args)))
        return 0
    finally:
        conn.close()


def main(argv=None):
    """CLI entry point (wired into the alems command tree in 39.5.6)."""
    p = argparse.ArgumentParser(description="Registered data corrections (39.5.1 1d)")
    p.add_argument("--store", required=True, help="path to experiments.db")
    p.add_argument("--only", default=",".join(CORRECTIONS), help="comma list, default all")
    p.add_argument("--platform-class", default=None, help="override host platform class")
    p.add_argument("--apply", action="store_true", help="backup, then write")
    a = p.parse_args(argv)
    only = [c.strip() for c in a.only.split(",") if c.strip()]
    unknown = [c for c in only if c not in CORRECTIONS]
    if unknown:
        print("unknown correction: %s" % ",".join(unknown))
        return 2
    return run(a.store, only, a.apply, a.platform_class)


if __name__ == "__main__":
    sys.exit(main())
