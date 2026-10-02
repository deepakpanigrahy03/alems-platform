"""
Persistence validation: INV-D1, INV-D2, coverage between run sets, write site lint.

Application API (C-CLI rule 8): functions return typed results; nothing here
prints for the user or calls sys.exit. The CLI layer maps results to exit codes.

Declarations live in persistence_keys.yaml beside this module. Every check
opens the store read only, so validation can never alter what it examines.

AUTHOR: Deepak Panigrahy
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

KEYS_PATH = os.path.join(os.path.dirname(__file__), "persistence_keys.yaml")
UNIQUE_INDEX = "unique_index"

# Subqueries that map an indirect link column to the runs it belongs to.
# "{runs}" is replaced by an id list or by "SELECT run_id FROM runs".
_INDIRECT = {
    "attempt": ("attempt_id", "SELECT attempt_id FROM goal_attempt WHERE run_id IN ({runs})"),
    "goal": ("goal_id", "SELECT goal_id FROM goal_attempt WHERE run_id IN ({runs})"),
    "span": ("span_id", "SELECT span_id FROM spans WHERE run_id IN ({runs})"),
    "sample": ("sample_id", "SELECT sample_id FROM energy_samples_v2 WHERE run_id IN ({runs})"),
}
_ALL_RUNS = "SELECT run_id FROM runs"

# Uppercase keywords only: SQL in this code base is written uppercase, and
# matching lowercase would flag prose such as "update runs" in comments.
_WRITE_RE = re.compile(r"(?:INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_][a-z0-9_]*)")


@dataclass(frozen=True)
class TableSpec:
    """Declared persistence contract of one run linked table."""

    name: str
    link: str
    run_columns: Tuple[str, ...]
    key: Optional[Tuple[str, ...]]
    unique_index: bool
    reason: str = ""
    repair_ignore: Tuple[str, ...] = ()


@dataclass
class Finding:
    """One violation or configuration error found by validation."""

    check: str          # INV-D1, INV-D2, UNDECLARED, CONFIG
    table: str
    count: int
    detail: str


@dataclass
class PersistenceReport:
    """Result of validate(); ok is False if any finding exists."""

    store: str
    evaluated: List[str] = field(default_factory=list)
    by_construction: List[str] = field(default_factory=list)
    not_evaluable: Dict[str, str] = field(default_factory=dict)
    absent: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no invariant violation and no undeclared table exists."""
        return not self.findings

    def to_dict(self) -> dict:
        """Plain dict for --json output (schema version 1)."""
        out = asdict(self)
        out["ok"] = self.ok
        out["schema"] = "alems.validate.persistence/1"
        return out


@dataclass
class CoverageRow:
    """Row counts of one table for two run sets."""

    table: str
    status: str         # OK, LOST, NEW, ERR
    old: Optional[int]
    new: Optional[int]


@dataclass
class Declarations:
    """Parsed persistence_keys.yaml."""

    tables: Dict[str, TableSpec]
    writers: Tuple[str, ...]
    exceptions: Dict[str, str]
    children: Dict[str, List[dict]] = field(default_factory=dict)


def load_declarations(path: str = KEYS_PATH) -> Declarations:
    """Parse the declaration file; raises ValueError on a malformed entry."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    tables = {name: _spec(name, body or {}) for name, body in (raw.get("tables") or {}).items()}
    writers = tuple(raw.get("writers") or ())
    exceptions = {str(k): str(v) for k, v in (raw.get("exceptions") or {}).items()}
    children = {p: list(c) for p, c in (raw.get("children") or {}).items()}
    for parent, items in children.items():
        _check_children(parent, items, tables)
    return Declarations(tables, writers, exceptions, children)


def _check_children(parent: str, items: List[dict], tables: Dict[str, TableSpec]) -> None:
    """Children used by the repair must name a known parent and a valid action."""
    if parent not in tables:
        raise ValueError("children: unknown parent %s" % parent)
    for c in items:
        if c.get("action") not in ("delete", "repoint") or not c.get("table") or not c.get("column"):
            raise ValueError("children of %s: need table, column, action delete|repoint" % parent)


def _spec(name: str, body: dict) -> TableSpec:
    """Build one TableSpec, checking the link kind and key form."""
    link = body.get("link")
    if link != "run" and link not in _INDIRECT:
        raise ValueError("table %s: unknown link %r" % (name, link))
    key = body.get("key")
    unique = key == UNIQUE_INDEX
    if key is None and not body.get("reason"):
        # A null key without a reason would hide a gap; the contract forbids it.
        raise ValueError("table %s: key null requires a reason" % name)
    key_cols = None if (unique or key is None) else tuple(key)
    run_cols = tuple(body.get("run_columns") or (("run_id",) if link == "run" else ()))
    ignore = tuple(body.get("repair_ignore") or ())
    return TableSpec(name, link, run_cols, key_cols, unique, body.get("reason", ""), ignore)


def open_ro(store: str) -> sqlite3.Connection:
    """Read only connection; validation must never write the store it checks.

    Raises FileNotFoundError for an empty or missing path: SQLite would
    otherwise open an empty temporary database and every check would pass.
    """
    if not store or not os.path.isfile(store):
        raise FileNotFoundError("store not found: %r" % (store,))
    return sqlite3.connect("file:%s?mode=ro" % store, uri=True)


def _columns(con: sqlite3.Connection, table: str) -> List[str]:
    """Column names of a table (empty if the table is absent)."""
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def _tables(con: sqlite3.Connection) -> List[str]:
    """All user tables of the store."""
    sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    return [r[0] for r in con.execute(sql)]


def discover_run_tables(con: sqlite3.Connection) -> List[str]:
    """Tables that hold a run reference by column name or declared foreign key."""
    found = []
    for t in _tables(con):
        if _has_run_reference(con, t):
            found.append(t)
    return sorted(found)


def _has_run_reference(con: sqlite3.Connection, table: str) -> bool:
    """run_id or *_run_id column (global_run_id excluded), or a FK to runs."""
    cols = _columns(con, table)
    if any(c == "run_id" or (c.endswith("_run_id") and c != "global_run_id") for c in cols):
        return True
    fks = con.execute("PRAGMA foreign_key_list(%s)" % table).fetchall()
    return any(fk[2] == "runs" for fk in fks)


def _scope(spec: TableSpec, runs: str) -> str:
    """WHERE clause selecting the rows of spec that belong to the runs subquery."""
    if spec.link == "run":
        return "(" + " OR ".join("%s IN (%s)" % (c, runs) for c in spec.run_columns) + ")"
    col, sub = _INDIRECT[spec.link]
    return "%s IN (%s)" % (col, sub.format(runs=runs))


def _orphan_sql(spec: TableSpec) -> str:
    """Count rows that reference no existing run (INV-D2)."""
    if spec.link != "run":
        # Indirect: the link value must resolve through its parent to a live run.
        col, sub = _INDIRECT[spec.link]
        return "SELECT COUNT(*) FROM %s WHERE %s IS NULL OR %s NOT IN (%s)" % (
            spec.name, col, col, sub.format(runs=_ALL_RUNS))
    # Multi run tables: at least one column set, and every set column must exist.
    none_set = " AND ".join("%s IS NULL" % c for c in spec.run_columns)
    dangling = " OR ".join("(%s IS NOT NULL AND %s NOT IN (%s))" % (c, c, _ALL_RUNS)
                           for c in spec.run_columns)
    return "SELECT COUNT(*) FROM %s WHERE (%s) OR %s" % (spec.name, none_set, dangling)


def _duplicate_sql(spec: TableSpec, where: str) -> str:
    """Number of duplicated keys and excess rows (INV-D1)."""
    keys = ", ".join(spec.key or ())
    return ("SELECT COUNT(*), COALESCE(SUM(n - 1), 0) FROM "
            "(SELECT COUNT(*) AS n FROM %s %s GROUP BY %s HAVING n > 1)" % (spec.name, where, keys))


def validate(store: str, runs: Optional[Sequence[int]] = None,
             decl: Optional[Declarations] = None) -> PersistenceReport:
    """Run INV-D1 (optionally scoped to runs), INV-D2 and the declaration check."""
    decl = decl or load_declarations()
    report = PersistenceReport(store=store)
    con = open_ro(store)
    try:
        present = set(_tables(con))
        if "runs" not in present:
            # Not an A-LEMS store; passing it would be a silent success.
            report.findings.append(Finding("CONFIG", "runs", 0, "store has no runs table"))
            return report
        _check_declared(con, decl, report)
        for spec in decl.tables.values():
            _check_table(con, spec, present, runs, report)
    finally:
        con.close()
    return report


def _check_declared(con: sqlite3.Connection, decl: Declarations, report: PersistenceReport) -> None:
    """Every discovered run linked table must be declared; new tables cannot slip in."""
    for t in discover_run_tables(con):
        if t not in decl.tables:
            report.findings.append(Finding("UNDECLARED", t, 0, "run linked table missing from persistence_keys.yaml"))


def _check_table(con, spec: TableSpec, present: set, runs, report: PersistenceReport) -> None:
    """Apply D1 and D2 to one declared table."""
    if spec.name not in present:
        # Extension tables may be absent from a store; reported, not failed.
        report.absent.append(spec.name)
        return
    missing = [c for c in (spec.key or ()) + spec.run_columns if c not in _columns(con, spec.name)]
    if missing:
        report.findings.append(Finding("CONFIG", spec.name, 0, "declared columns absent: %s" % ",".join(missing)))
        return
    _check_orphans(con, spec, report)
    _check_duplicates(con, spec, runs, report)


def _check_orphans(con, spec: TableSpec, report: PersistenceReport) -> None:
    """INV-D2 over the whole store (orphans cannot be scoped by run)."""
    n = con.execute(_orphan_sql(spec)).fetchone()[0]
    if n:
        report.findings.append(Finding("INV-D2", spec.name, n, "rows without an existing run"))


def _check_duplicates(con, spec: TableSpec, runs, report: PersistenceReport) -> None:
    """INV-D1; unique indexes hold by construction, null keys are not evaluable."""
    if spec.unique_index:
        report.by_construction.append(spec.name)
        return
    if spec.key is None:
        report.not_evaluable[spec.name] = spec.reason
        return
    where = "WHERE " + _scope(spec, ",".join(str(int(r)) for r in runs)) if runs else ""
    keys, excess = con.execute(_duplicate_sql(spec, where)).fetchone()
    report.evaluated.append(spec.name)
    if keys:
        report.findings.append(Finding("INV-D1", spec.name, excess, "%d keys stored more than once" % keys))


def coverage(store: str, old_runs: Sequence[int], new_runs: Sequence[int],
             decl: Optional[Declarations] = None) -> List[CoverageRow]:
    """Row counts per declared table for two run sets (Rule S persistence proof)."""
    decl = decl or load_declarations()
    con = open_ro(store)
    try:
        present = set(_tables(con))
        specs = [s for s in decl.tables.values() if s.name in present]
        return [_coverage_row(con, s, old_runs, new_runs) for s in sorted(specs, key=lambda s: s.name)]
    finally:
        con.close()


def _coverage_row(con, spec: TableSpec, old_runs, new_runs) -> CoverageRow:
    """Classify one table: OK, LOST (old rows, none new), NEW, or ERR."""
    try:
        old = _count(con, spec, old_runs)
        new = _count(con, spec, new_runs)
    except sqlite3.Error as exc:
        return CoverageRow(spec.name, "ERR " + str(exc), None, None)
    if old > 0 and new == 0:
        return CoverageRow(spec.name, "LOST", old, new)
    status = "NEW" if (old == 0 and new > 0) else "OK"
    return CoverageRow(spec.name, status, old, new)


def _count(con, spec: TableSpec, runs: Sequence[int]) -> int:
    """Rows of spec belonging to the run set."""
    ids = ",".join(str(int(r)) for r in runs)
    return con.execute("SELECT COUNT(*) FROM %s WHERE %s" % (spec.name, _scope(spec, ids))).fetchone()[0]


def lint_write_sites(root: str, decl: Optional[Declarations] = None,
                     subdirs: Sequence[str] = ("core", "scripts")) -> List[Tuple[str, int, str]]:
    """Write statements against run linked tables outside writers and exceptions."""
    decl = decl or load_declarations()
    allowed = tuple(decl.writers) + tuple(decl.exceptions)
    hits = []
    for rel in _python_files(root, subdirs):
        if rel.startswith(allowed) or "/tests/" in rel or os.path.basename(rel).startswith("test_"):
            continue
        hits.extend(_scan_file(root, rel, decl.tables))
    return hits


def _python_files(root: str, subdirs: Sequence[str]) -> List[str]:
    """Relative paths of every .py file under the given subdirectories."""
    out = []
    for sub in subdirs:
        for d, _dirs, files in os.walk(os.path.join(root, sub)):
            out.extend(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                       for f in files if f.endswith(".py"))
    return sorted(out)


def _scan_file(root: str, rel: str, tables: Dict[str, TableSpec]) -> List[Tuple[str, int, str]]:
    """(file, line, table) for every write to a declared table in one file."""
    hits = []
    with open(os.path.join(root, rel), "r", encoding="utf-8", errors="replace") as fh:
        for no, line in enumerate(fh, 1):
            hits.extend((rel, no, t) for t in _WRITE_RE.findall(line) if t in tables)
    return hits
