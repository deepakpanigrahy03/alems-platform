"""
INV-D1 structural guard: the UNIQUE indexes of v117 and of the fresh install
constant match the declared natural keys, and a duplicate insert is refused.

AUTHOR: Deepak Panigrahy
"""
import os
import re
import sqlite3

from core.database.schema import CREATE_NATURAL_KEY_INDEXES
from core.validation.persistence import load_declarations

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IDX = re.compile(r"CREATE UNIQUE INDEX IF NOT EXISTS idx_nk_(\w+)\s+ON (\w+)\(([^)]*)\)")


def _indexes(sql):
    """{table: (key columns)} from CREATE UNIQUE INDEX statements."""
    return {t: tuple(c.strip() for c in cols.split(",")) for _n, t, cols in _IDX.findall(sql)}


def test_fresh_install_equals_migration():
    """SC-1: schema.py constant and v117 declare the same indexes."""
    with open(os.path.join(REPO, "migrations/schema/v117_natural_key_unique_indexes.sql")) as fh:
        assert _indexes(fh.read()) == _indexes(CREATE_NATURAL_KEY_INDEXES)


def test_indexes_match_declared_keys():
    """The index key of every table equals its key in persistence_keys.yaml."""
    decl = load_declarations()
    for table, cols in _indexes(CREATE_NATURAL_KEY_INDEXES).items():
        assert decl.tables[table].key == cols, table


def test_duplicate_insert_refused(tmp_path):
    """With the index in place a double write raises IntegrityError."""
    con = sqlite3.connect(str(tmp_path / "x.db"))
    con.execute("CREATE TABLE interrupt_samples (sample_id INTEGER PRIMARY KEY, run_id INT, timestamp_ns INT)")
    stmt = [s for s in CREATE_NATURAL_KEY_INDEXES.split(";") if "interrupt_samples" in s][0]
    con.execute(stmt)
    con.execute("INSERT INTO interrupt_samples (run_id, timestamp_ns) VALUES (1, 5)")
    try:
        con.execute("INSERT INTO interrupt_samples (run_id, timestamp_ns) VALUES (1, 5)")
        raise AssertionError("duplicate accepted")
    except sqlite3.IntegrityError:
        pass
