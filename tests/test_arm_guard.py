"""RAF R6 runner guard: a profile naming an arm without a decider is refused."""

import pytest

from core.execution.scheduled_runner import check_arms


def _profile(options):
    return {"schedule": {"families": {"clean": {"options": options}}}}


def test_missing_arm_refused():
    with pytest.raises(SystemExit) as e:
        check_arms(_profile(["SR_warm@ctrl", "FR_warm"]), {"escalate": object()})
    assert "ctrl" in str(e.value)


def test_declared_arm_and_fixed_accepted():
    check_arms(_profile(["SR_warm@ctrl", "FR_warm", "SR_warm@escalate"]),
               {"ctrl": object(), "escalate": object()})


def test_profile_without_schedule_accepted():
    check_arms({}, {})
