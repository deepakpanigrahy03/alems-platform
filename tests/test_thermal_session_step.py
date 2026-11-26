"""Sequencer: profiling kinds run inside the attempt scope; the thermal_session step kind."""
import pytest
from alems_sdk.sequencer import StepFailed, StepKindABC, Availability

from core.cli import cmd_thermal
from core.cli.cmd_thermal import ThermalSessionStep
from core.sequencer.context import current_attempt
from core.sequencer.kinds import _profiling_outcome


class _Seen(StepKindABC):
    KIND, MEASUREMENT = "seen", "profiling"

    def is_available(self, step, ctx):
        return Availability(True)

    def measure(self, step, ctx):
        return {"attempt": (current_attempt(), None)}


def test_profiling_kind_runs_inside_the_attempt_scope():
    out = _profiling_outcome(_Seen(), {}, {"attempt_id": 41, "cancelled": lambda: False}, StepFailed)
    assert out["status"] == "completed" and out["results"]["attempt"] == (41, None)


def test_validate_refuses_missing_profile_and_profile_without_plan(tmp_path):
    assert ThermalSessionStep.validate({}) == ["thermal_session: missing parameter profile"]
    assert "not found" in ThermalSessionStep.validate({"profile": str(tmp_path / "none.yaml")})[0]
    p = tmp_path / "plain.yaml"
    p.write_text("tasks: []\n")
    assert "no session_plan" in ThermalSessionStep.validate({"profile": str(p)})[0]


def test_availability_and_failed_exit(tmp_path, monkeypatch):
    p = tmp_path / "x.yaml"
    assert ThermalSessionStep().is_available({"profile": str(p)}, {}).code == "profile_missing"
    p.write_text("session_plan: {plugin: back_to_back}\n")
    assert ThermalSessionStep().is_available({"profile": str(p)}, {}).ok
    monkeypatch.setattr(cmd_thermal, "_run_profile", lambda path: 3)
    with pytest.raises(StepFailed) as exc:
        ThermalSessionStep().measure({"profile": str(p)}, {"attempt_id": 1, "cancelled": lambda: False})
    assert exc.value.code == "profile_exit"
    monkeypatch.setattr(cmd_thermal, "_run_profile", lambda path: 0)
    assert ThermalSessionStep().measure({"profile": str(p)}, {})["exit_code"] == (0, None)
