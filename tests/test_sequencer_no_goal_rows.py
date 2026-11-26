"""Sequencer measurement runs (idle brackets, request profiles) are runs, not goals (45.5e)."""
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_save_single_records_goal_by_default():
    from core.execution.experiment_runner import ExperimentRunner
    p = inspect.signature(ExperimentRunner.save_single).parameters["record_goal"]
    assert p.default is True


def test_sequencer_measured_run_skips_goal_rows():
    src = (ROOT / "core" / "sequencer" / "benchmark.py").read_text()
    assert 'save_single(db, exp_id, rt["hw_id"], result, 1, "linear", record_goal=False)' in src


def test_no_invented_failure_type_without_information():
    src = (ROOT / "core" / "execution" / "experiment_runner.py").read_text()
    assert "_failure_classifier.classify(run_result=None)" not in src
