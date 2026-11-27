"""Integrity check skips goal checks for sequencer measurement steps only."""
import importlib.util
import sqlite3
from pathlib import Path

from core.sequencer.plan import KNOWN_KINDS_CORE

_SPEC = importlib.util.spec_from_file_location(
    "test_exp_integrity", Path(__file__).resolve().parents[1] / "scripts" / "test_exp_integrity.py")
integrity = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(integrity)


def _store(kind):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE energy_validation_seq_steps (step_id INTEGER PRIMARY KEY, kind TEXT, role TEXT)")
    conn.execute("CREATE TABLE energy_validation_seq_attempts (attempt_id INTEGER PRIMARY KEY, step_id INTEGER, exp_id INTEGER)")
    conn.execute("INSERT INTO energy_validation_seq_steps VALUES (1, ?, 'pre')", (kind,))
    conn.execute("INSERT INTO energy_validation_seq_attempts VALUES (1, 1, 20)")
    return conn


def test_idle_step_is_measurement():
    assert integrity.get_measurement_kind(_store("idle"), 20) == "idle"


def test_goal_step_is_checked():
    assert integrity.get_measurement_kind(_store("thermal_session"), 20) is None


def test_experiment_without_step_is_checked():
    assert integrity.get_measurement_kind(_store("idle"), 99) is None


def test_store_without_sequencer_tables_is_checked():
    assert integrity.get_measurement_kind(sqlite3.connect(":memory:"), 20) is None


def test_measurement_kinds_cover_core_kinds():
    assert set(KNOWN_KINDS_CORE) <= set(integrity.MEASUREMENT_KINDS)
