# tests/test_audit_system.py
# Tests for the A-LEMS audit regression kit (SPEC_39_1 section 7).
# Does not touch the live DB.
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


def test_audit_runner_imports():
    from scripts.tools.audit.runner import audit_main
    assert callable(audit_main)


def test_audit_utils_imports():
    from scripts.tools.audit.utils import (
        load_ownership, dump_all, table_hash, diff_dumps,
        check_coverage, get_db_path, AUDIT_DIR, REPORTS_DIR,
    )
    for fn in (load_ownership, dump_all, table_hash, diff_dumps,
               check_coverage, get_db_path):
        assert callable(fn)


def test_load_ownership_covers_tables():
    from scripts.tools.audit.utils import load_ownership
    ownership = load_ownership()
    tables = [k for k, v in ownership.items() if v["kind"] == "table"]
    views = [k for k, v in ownership.items() if v["kind"] == "view"]
    assert len(tables) >= 86, f"Expected >=86 tables, got {len(tables)}"
    assert len(views) >= 35, f"Expected >=35 views, got {len(views)}"


def test_audit_dir_is_under_project():
    """AUDIT_DIR sits alongside experiments.db not inside the repo."""
    from scripts.tools.audit.utils import AUDIT_DIR, REPO_ROOT
    assert str(REPO_ROOT) not in str(AUDIT_DIR), (
        f"AUDIT_DIR should not be inside REPO_ROOT.\n"
        f"  REPO_ROOT={REPO_ROOT}\n  AUDIT_DIR={AUDIT_DIR}"
    )


def test_diff_identical_returns_empty_changed():
    from scripts.tools.audit.utils import diff_dumps
    dump = {
        "__schema__": [],
        "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": 1000.0}],
    }
    ownership = {"runs": {"kind": "table", "golden_class": "derived",
                          "natural_key": ["exp_id", "run_number"],
                          "run_linkage": "run_id"}}
    result = diff_dumps(dump, dump, ownership)
    assert result["changed"] == []
    assert "runs" in result["unchanged"]


def test_diff_detects_cell_change():
    from scripts.tools.audit.utils import diff_dumps
    b = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": 1000.0}]}
    c = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": 1001.0}]}
    ownership = {"runs": {"kind": "table", "golden_class": "derived",
                          "natural_key": ["exp_id", "run_number"],
                          "run_linkage": "run_id"}}
    result = diff_dumps(b, c, ownership)
    cells = [d for d in result["changed"] if d["kind"] == "cell"]
    assert len(cells) == 1
    assert cells[0]["col"] == "total_energy_uj"
    assert cells[0]["old"] == 1000.0
    assert cells[0]["new"] == 1001.0


def test_diff_float_within_tolerance():
    from scripts.tools.audit.utils import diff_dumps
    v = 1234567.89
    v2 = v * (1 + 5e-10)
    b = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": v}]}
    c = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": v2}]}
    ownership = {"runs": {"kind": "table", "golden_class": "derived",
                          "natural_key": ["exp_id", "run_number"],
                          "run_linkage": "run_id"}}
    result = diff_dumps(b, c, ownership)
    assert result["changed"] == []


def test_diff_new_column_shows_in_changed():
    from scripts.tools.audit.utils import diff_dumps
    b = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": 1000.0}]}
    c = {"__schema__": [],
         "runs": [{"exp_id": 1, "run_number": 1, "total_energy_uj": 1000.0,
                   "new_col": 42}]}
    ownership = {"runs": {"kind": "table", "golden_class": "derived",
                          "natural_key": ["exp_id", "run_number"],
                          "run_linkage": "run_id"}}
    result = diff_dumps(b, c, ownership)
    cells = [d for d in result["changed"] if d["kind"] == "cell"]
    assert any(d["col"] == "new_col" for d in cells)


def test_table_hash_deterministic():
    from scripts.tools.audit.utils import table_hash
    rows = [{"exp_id": 1, "run_number": 1, "val": 42.0},
            {"exp_id": 1, "run_number": 2, "val": None}]
    assert table_hash(rows) == table_hash(rows)
    assert len(table_hash(rows)) == 64


def test_check_coverage_catches_unlisted_table():
    from scripts.tools.audit.utils import check_coverage
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unlisted_table (x INTEGER)")
        conn.commit()
        violations = check_coverage(conn, {})
        conn.close()
    assert any("unlisted_table" in v for v in violations)


def test_reference_runs_yaml_exists_and_valid():
    import yaml
    p = Path(__file__).resolve().parents[1] / "audit" / "gn100-2b96" / "reference_runs.yaml"
    assert p.exists(), f"Not found: {p}"
    with open(p) as fh:
        data = yaml.safe_load(fh)
    runs = data.get("runs", [])
    assert len(runs) == 12, f"Expected 12 runs, got {len(runs)}"
    for r in runs:
        assert "run_id" in r
        assert "persistence_path" in r
        assert r["persistence_path"] in ("save_pair", "execute_goal")
