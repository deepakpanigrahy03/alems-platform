"""Single mode (save_single) outcome reads agentic status at either level, like save_pair (45.5g)."""
from core.execution.experiment_runner import _execution_succeeded


def test_linear_status_at_top_level():
    assert _execution_succeeded({"execution": {"status": "success"}})
    assert not _execution_succeeded({"execution": {"status": "failure"}})


def test_agentic_status_nested_one_level_deeper():
    assert _execution_succeeded({"execution": {"execution": {"status": "success"}}})
    assert not _execution_succeeded({"execution": {"execution": {"status": "failure"}}})


def test_missing_or_empty_is_failure():
    assert not _execution_succeeded({})
    assert not _execution_succeeded({"execution": None})
    assert not _execution_succeeded({"execution": {"execution": None}})
