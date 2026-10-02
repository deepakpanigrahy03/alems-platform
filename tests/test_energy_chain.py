"""
tests/test_energy_chain.py (39.5.1 1d.2, E4)

One rule for goal and attempt energy on all three paths: attributed energy,
a measured 0 kept, None when absent, never a substitute (INV-E5, INV-E1).
"""

import pathlib
import sqlite3
import types

from core.database.repositories import tax as tax_module
from core.execution.run_persistence import (
    attributed_energy_or_none,
    orchestration_energy_or_none,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _result(ml=None, l3=None):
    r = {"ml_features": ml or {}}
    if l3 is not None:
        r["layer3_derived"] = {"energy_uj": l3}
    return r


def test_attributed_present():
    assert attributed_energy_or_none(_result({"attributed_energy_uj": 1234})) == 1234


def test_attributed_measured_zero_kept():
    assert attributed_energy_or_none(_result({"attributed_energy_uj": 0})) == 0


def test_attributed_absent_is_none_never_substituted():
    # The old chain returned dynamic x fraction here (5000) or layer3 workload.
    r = _result({"dynamic_energy_uj": 10000, "cpu_fraction": 0.5}, {"workload": 9})
    assert attributed_energy_or_none(r) is None


def test_result_none():
    assert attributed_energy_or_none(None) is None
    assert orchestration_energy_or_none(None) is None


def test_orchestration():
    assert orchestration_energy_or_none(_result(l3={"orchestration_tax": 77})) == 77
    assert orchestration_energy_or_none(_result(l3={})) is None
    assert orchestration_energy_or_none(_result()) is None


def _tax_repo():
    cls = next(v for v in vars(tax_module).values()
               if isinstance(v, type) and hasattr(v, "create_tax_summary_for_pair"))
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE orchestration_tax_summary (
        linear_run_id, agentic_run_id, linear_dynamic_uj, agentic_dynamic_uj,
        orchestration_tax_uj, tax_percent, linear_orchestration_uj, agentic_orchestration_uj)""")
    repo = object.__new__(cls)
    repo.db = types.SimpleNamespace(conn=conn)
    return repo, conn


def test_tax_unknown_side_is_null():
    repo, conn = _tax_repo()
    repo.create_tax_summary_for_pair(1, 2, None, 500, None, 10)
    row = conn.execute("SELECT orchestration_tax_uj, tax_percent FROM orchestration_tax_summary").fetchone()
    assert row == (None, None)


def test_tax_known_sides_unchanged_and_zero_agentic_is_null():
    repo, conn = _tax_repo()
    repo.create_tax_summary_for_pair(1, 2, 100, 400, 0, 0)
    repo.create_tax_summary_for_pair(3, 4, 100, 0, 0, 0)
    rows = conn.execute("SELECT orchestration_tax_uj, tax_percent FROM orchestration_tax_summary").fetchall()
    assert rows[0] == (300, 75.0)
    assert rows[1] == (-100, None)


def test_parity_three_paths_use_one_rule():
    # EPS-1: save_pair and save_single (experiment_runner) and execute_goal.
    er = (ROOT / "core/execution/experiment_runner.py").read_text()
    gm = (ROOT / "core/execution/goal_execution_manager.py").read_text()
    assert er.count("attributed_energy_or_none(") >= 3
    assert "attributed_energy_or_none(" in gm
    for dead in ("_get_attributed", "def _extract_energy", "accumulated_energy"):
        assert dead not in er and dead not in gm, dead


def test_goal_total_unknown_attempt_is_null():
    from core.attribution.legacy_v1 import goal_execution_etl as g
    # (attempt_id, run_id, is_winning, energy_uj, gpu_energy_uj)
    assert g._sum_attempt_energies([(1, 9, 0, 100, None), (2, 9, 1, 50, None)]) == 150
    assert g._sum_attempt_energies([(1, 9, 0, None, None), (2, 9, 1, 50, None)]) is None
    assert g._sum_attempt_energies([(1, 9, 1, 0, None)]) == 0
    assert g._sum_known_or_none([None, None]) is None
    assert g._sum_known_or_none([]) is None
