"""
Duplicate repair for INV-D1: plan (read only) and apply.

A row is removed only if it equals the kept row (lowest rowid of its natural
key) in every column except the primary key and the table's declared
repair_ignore columns (timestamps and columns filled after insert, such as an
ETL or backfill that updated only one copy). Before removal, NULLs of those
columns in the kept row are filled from the removed copy, so nothing written
later is lost. Key matches with other differences are conflicts and are
never removed. Children of removed rows are
deleted or repointed to the kept row as declared in persistence_keys.yaml
(section children), so INV-D2 still holds after the repair.

plan_dedupe reads only. apply_plan takes a connection or writer cursor that
the caller owns: scratch copies during the expected diff, the store writer
for the approved correction.

AUTHOR: Deepak Panigrahy
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from core.validation.persistence import Declarations, load_declarations, open_ro


@dataclass
class TablePlan:
    """What the repair would do to one table."""

    table: str
    remove: List[Tuple[int, int]] = field(default_factory=list)   # (duplicate rowid, kept rowid)
    conflicts: int = 0
    runs: List[int] = field(default_factory=list)


@dataclass
class RepairPlan:
    """Whole plan; summary() is the reviewable form."""

    store: str
    tables: Dict[str, TablePlan] = field(default_factory=dict)

    def summary(self) -> List[Tuple[str, int, int, int]]:
        """(table, rows to remove, conflicts, runs affected) per table."""
        return [(t, len(p.remove), p.conflicts, len(p.runs)) for t, p in sorted(self.tables.items())]


def _pk_columns(con: sqlite3.Connection, table: str) -> List[str]:
    """Primary key columns; they differ between a row and its duplicate."""
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table) if r[5]]


def _content_columns(con: sqlite3.Connection, table: str) -> List[str]:
    """Every column except the primary key."""
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table) if not r[5]]


def plan_dedupe(store: str, tables: Optional[Sequence[str]] = None,
                decl: Optional[Declarations] = None) -> RepairPlan:
    """Read only: duplicates to remove per table, identical content only."""
    decl = decl or load_declarations()
    names = tables or [s.name for s in decl.tables.values() if s.key]
    plan = RepairPlan(store=store)
    con = open_ro(store)
    try:
        for name in names:
            cols = set(_content_columns(con, name)) | set(_pk_columns(con, name))
            if not cols:
                # Table absent from this store (older or partial schema): nothing to plan.
                continue
            missing = [c for c in decl.tables[name].key if c not in cols]
            if missing:
                # Declared key not in this store's schema: refuse rather than guess.
                plan.tables[name] = TablePlan(name, conflicts=-1)
                continue
            plan.tables[name] = _plan_table(con, decl.tables[name])
    finally:
        con.close()
    return plan


def _plan_table(con: sqlite3.Connection, spec) -> TablePlan:
    """Find duplicate rows of one table and split them into removable and conflicts."""
    key = list(spec.key)
    content = [c for c in _content_columns(con, spec.name) if c not in spec.repair_ignore]
    on_key = " AND ".join("t.%s IS d.%s" % (c, c) for c in key)
    same = " AND ".join("t.%s IS k.%s" % (c, c) for c in content)
    run_col = "t.run_id" if "run_id" in content else "NULL"
    # d: one row per duplicated key with the rowid to keep (lowest).
    sql = ("SELECT t.rowid, d.keep, (SELECT 1 FROM {t} k WHERE k.rowid = d.keep AND {same}), {run} "
           "FROM {t} t JOIN (SELECT {keys}, MIN(rowid) AS keep FROM {t} GROUP BY {keys} HAVING COUNT(*) > 1) d "
           "ON {on} WHERE t.rowid <> d.keep").format(
        t=spec.name, same=same, run=run_col, keys=", ".join(key), on=on_key)
    tp = TablePlan(spec.name)
    runs = set()
    for rowid, keep, identical, run_id in con.execute(sql):
        if identical:
            tp.remove.append((rowid, keep))
            runs.add(run_id)
        else:
            tp.conflicts += 1
    tp.runs = sorted(r for r in runs if r is not None)
    return tp


def apply_plan(con: sqlite3.Connection, plan: RepairPlan, decl: Optional[Declarations] = None) -> Dict[str, int]:
    """Apply a plan on a connection the caller owns (one transaction per call).

    Children first (repoint or delete), then the duplicates. Returns rows
    removed or repointed per table. The caller commits or rolls back.
    """
    decl = decl or load_declarations()
    done: Dict[str, int] = {}
    for name, tp in plan.tables.items():
        if not tp.remove:
            continue
        pk = _pk_columns(con, name)
        _merge_late_columns(con, name, decl.tables[name].repair_ignore, tp.remove)
        _apply_children(con, name, pk, tp.remove, decl, done)
        con.executemany("DELETE FROM %s WHERE rowid = ?" % name, [(r,) for r, _k in tp.remove])
        done[name] = done.get(name, 0) + len(tp.remove)
    return done


def _merge_late_columns(con, table: str, columns, pairs) -> None:
    """Fill NULLs of late written columns in the kept row from the duplicate."""
    for col in columns:
        sql = ("UPDATE {t} SET {c} = (SELECT {c} FROM {t} WHERE rowid = ?) "
               "WHERE rowid = ? AND {c} IS NULL").format(t=table, c=col)
        con.executemany(sql, [(dup, keep) for dup, keep in pairs])


def _apply_children(con, parent: str, pk: List[str], pairs, decl: Declarations, done: Dict[str, int]) -> None:
    """Repoint or delete children that reference a duplicate parent row."""
    for child in decl.children.get(parent, []):
        if len(pk) != 1:
            raise ValueError("children of %s need a single column primary key" % parent)
        ids = _pk_values(con, parent, pk[0], pairs)
        if child["action"] == "repoint":
            sql = "UPDATE %s SET %s = ? WHERE %s = ?" % (child["table"], child["column"], child["column"])
            n = sum(con.execute(sql, (keep, dup)).rowcount for dup, keep in ids)
        else:
            sql = "DELETE FROM %s WHERE %s = ?" % (child["table"], child["column"])
            n = sum(con.execute(sql, (dup,)).rowcount for dup, _keep in ids)
        key = "%s(%s)" % (child["table"], child["action"])
        done[key] = done.get(key, 0) + n


def _pk_values(con, table: str, pk: str, pairs) -> List[Tuple[object, object]]:
    """Map (duplicate rowid, kept rowid) to (duplicate pk, kept pk)."""
    get = "SELECT %s FROM %s WHERE rowid = ?" % (pk, table)
    return [(con.execute(get, (d,)).fetchone()[0], con.execute(get, (k,)).fetchone()[0]) for d, k in pairs]
