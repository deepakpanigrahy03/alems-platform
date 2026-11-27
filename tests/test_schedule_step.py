"""schedule_load step kind (Paper 5): results, failures and observed bytes, with a fake schedule process."""
import json

import pytest

from alems_plugin_pravaha import schedule_step as ss
from alems_sdk.sequencer import StepFailed

LINES = ['SETUP {"t0": 10, "t1": 20, "attributes": {}}',
         'SLOT {"index": 0, "name": "resident", "kind": "kernel", "t0": 100, "t1": 200, "ops": 128, '
         '"payload_bytes": 1073741824, "check": {"ok": true}, "detail": {}}',
         'HALF {"slot": 1, "period": 0, "half": "on", "t0": 300, "t1": 400, "ops": 5, "payload_bytes": 83886080}',
         'HALF {"slot": 1, "period": 0, "half": "off", "t0": 400, "t1": 500, "ops": 5, "payload_bytes": 0}',
         'SLOT {"index": 1, "name": "square", "kind": "square", "t0": 300, "t1": 500, "ops": 10, '
         '"payload_bytes": 83886080, "check": null, "detail": {}}',
         'DONE {"slots": 2, "checks_ok": true, "payload_bytes": 1157627904}']


@pytest.fixture
def fake(monkeypatch):
    state = {"rc": 0, "lines": LINES}
    monkeypatch.setattr(ss, "run_schedule", lambda path, dev, to: (state["rc"], state["lines"], "err"))
    return state


STEP = {"schedule": "/tmp/s.json", "timeout_s": 60, "observed_fields": "kernel_read_miss_bytes"}


def test_results_have_slot_and_half_windows(fake):
    r = ss.ScheduleLoadStep().measure(STEP, {})
    assert r["n_slots"] == (2, None) and r["n_halves"] == (2, None)
    assert r["slot_00.t0_ns"] == (100, "ns") and r["slot_00.check_ok"] == (True, None)
    assert "slot_01.check_ok" not in r
    assert r["half_0001.half"] == ("off", None) and r["half_0000.payload_bytes"] == (83886080, "B")


def test_failed_check_and_exit_fail_the_step(fake):
    fake["rc"] = 3
    with pytest.raises(StepFailed) as e:
        ss.ScheduleLoadStep().measure(STEP, {})
    assert e.value.code == "check_failed"
    fake["rc"], fake["lines"] = 0, LINES[:-1]   # no DONE line
    with pytest.raises(StepFailed):
        ss.ScheduleLoadStep().measure(STEP, {})


def test_evaluate_observed_bytes_per_slot(fake, monkeypatch):
    r = ss.ScheduleLoadStep().measure(STEP, {})
    monkeypatch.setattr(ss.window, "bytes_in", lambda samples, a, b, f: float(b - a))
    run = {"traffic": [{"status": "measured", "samples": [{"t": 1}]}]}
    ev = ss.ScheduleLoadStep().evaluate(STEP, {}, r, run)
    assert ev == {"slot_00.obs_kernel_read_miss_bytes": (100.0, "B"), "slot_01.obs_kernel_read_miss_bytes": (200.0, "B")}
    assert ss.ScheduleLoadStep().evaluate(dict(STEP, observed_fields=""), {}, r, run) == {}
