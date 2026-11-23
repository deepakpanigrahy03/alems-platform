"""
tests/test_declared_dedicated_machine.py

experiments.declared_dedicated_machine: stored from the study config as a
declaration (1, 0 or NULL), shown by the validator as declared, never as
verified isolation.
"""

import contextlib
import io
import pathlib
import sqlite3
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.database.schema import CREATE_EXPERIMENTS                  # noqa: E402
from core.database.sqlite_adapter import SQLiteAdapter               # noqa: E402
from core.execution.experiment_config_loader import _apply_study_section  # noqa: E402
import report_energy_chain as report                                 # noqa: E402

COLUMN_DDL = "declared_dedicated_machine INTEGER CHECK(declared_dedicated_machine IN (0, 1))"


def _adapter():
    """Adapter on an in memory store with only the experiments table."""
    a = SQLiteAdapter.__new__(SQLiteAdapter)
    a.conn = sqlite3.connect(":memory:")
    a.conn.executescript(CREATE_EXPERIMENTS)
    return a


def _stored(a, exp_id):
    return a.conn.execute("SELECT declared_dedicated_machine FROM experiments "
                          "WHERE exp_id = ?", (exp_id,)).fetchone()[0]


def test_flag_stored_from_config():
    a = _adapter()
    args = SimpleNamespace()
    _apply_study_section(args, {"declared_dedicated_machine": True})
    exp_id = a.insert_experiment({"name": "t", "experiment_type": "normal",
                                  "declared_dedicated_machine": args.declared_dedicated_machine})
    assert _stored(a, exp_id) == 1


def test_declared_shared_is_zero():
    args = SimpleNamespace()
    _apply_study_section(args, {"declared_dedicated_machine": False})
    assert args.declared_dedicated_machine == 0


def test_absent_is_null_never_zero():
    a = _adapter()
    args = SimpleNamespace()
    _apply_study_section(args, {})
    assert not hasattr(args, "declared_dedicated_machine")
    exp_id = a.insert_experiment({"name": "t", "experiment_type": "normal"})
    assert _stored(a, exp_id) is None


def test_invalid_value_rejected_by_store():
    a = _adapter()
    try:
        a.insert_experiment({"name": "t", "experiment_type": "normal",
                             "declared_dedicated_machine": 2})
    except sqlite3.IntegrityError:
        return
    raise AssertionError("value 2 should violate the CHECK constraint")


def _metadata_text(flag):
    exp = {"exp_id": 1, "experiment_type": "normal", "workflow_type": "comparison",
           "runs_completed": 0, "is_valid": 1, "runs": [],
           "declared_dedicated_machine": flag}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report.render_metadata(exp)
    return buf.getvalue()


def test_validator_shows_declared_not_verified():
    assert "declared dedicated" in _metadata_text(1)
    assert "not verified" in _metadata_text(1)
    assert "not declared" in _metadata_text(None)


def test_no_verified_isolation_wording_anywhere():
    for rel in ("scripts/report_energy_chain.py", "scripts/validate_energy_chain_v2.py",
                "core/database/schema.py", "core/execution/experiment_runner.py"):
        text = (ROOT / rel).read_text().lower()
        assert "verified single workload" not in text, rel
        # "never/not verified isolation" are the required disclaimers, not claims
        text = text.replace("not verified isolation", "").replace("never verified isolation", "")
        assert "verified isolation" not in text, rel


def test_migration_matches_schema():
    """Fresh install DDL (schema.py) and migration define the same column."""
    mig = (ROOT / "migrations/schema/v124_experiments_declared_dedicated_machine.sql").read_text()
    assert COLUMN_DDL in mig
    assert COLUMN_DDL in CREATE_EXPERIMENTS
