"""Tests for the copy_rate step (no GPU): sizing, results, control window, proration, steadiness."""
import pytest

from alems_plugin_pravaha import copy_step as cs
from alems_sdk.sequencer import StepFailed

S = 1_000_000_000  # 1 s in ns


def _samples(t_start, n, per_10ms):
    return [{"t_start_ns": t_start + i * 10_000_000, "t_end_ns": t_start + (i + 1) * 10_000_000,
             "copy_bytes": per_10ms(i)} for i in range(n)]


def test_validate_sizes_from_config(monkeypatch):
    monkeypatch.setenv("ALEMS_COPY_SIZES_MIB", "16,256")
    assert any("size_mib" in e for e in cs.CopyRateStep.validate({"size_mib": 128, "direction": "h2d"}))
    assert cs.CopyRateStep.validate({"size_mib": 256, "direction": "h2d"}) == []


def test_prepare_sizes_times(monkeypatch):
    monkeypatch.setattr(cs, "run_known_bytes", lambda d, n, s, t, pause_s=None: (0, 0, 1 * S, ""))
    prep = cs.CopyRateStep().prepare({"size_mib": 16, "direction": "h2d", "duration_s": 60}, {})
    assert prep["times"] == 240 and prep["probe_rate_bps"] == 4 * 16 * cs.MIB


def test_measure_results_and_failure(monkeypatch):
    step = {"size_mib": 16, "direction": "h2d", "duration_s": 60}
    prep = {"times": 240, "probe_rate_bps": 1.0, "duration_s": 60}
    monkeypatch.setattr(cs, "run_known_bytes", lambda *a, **k: (0, 10 * S, 70 * S, ""))
    r = cs.CopyRateStep().measure(step, {"prepared": prep})
    assert r["completed_bytes"][0] == 240 * 16 * cs.MIB and r["duration_ok"][0] is True
    assert r["target_rate_bps"][0] is None
    monkeypatch.setattr(cs, "run_known_bytes", lambda *a, **k: (1, None, None, "CUDA error 2"))
    with pytest.raises(StepFailed) as e:
        cs.CopyRateStep().measure(step, {"prepared": prep})
    assert e.value.code == "workload_failed"


def test_control_window_is_held_pause(monkeypatch):
    step = {"size_mib": 16, "direction": "h2d", "duration_s": 60, "control": True}
    prep = cs.CopyRateStep().prepare(step, {})
    seen = {}

    def fake(d, n, s, t, pause_s=None):
        seen["pause"], seen["n"] = pause_s, n
        return (0, 100 * S, 100 * S + 1000, "")

    monkeypatch.setattr(cs, "run_known_bytes", fake)
    r = cs.CopyRateStep().measure(step, {"prepared": prep})
    assert seen == {"pause": 60.0, "n": 0}
    assert (r["work_t0_ns"][0], r["work_t1_ns"][0]) == (40 * S, 100 * S) and r["achieved_rate_bps"][0] is None


def test_bytes_prorated_at_edges():
    s = _samples(0, 3, lambda i: 100)               # 0..30 ms, 100 B each
    assert cs._bytes_in(s, 5_000_000, 25_000_000) == pytest.approx(200.0)


def test_steadiness_outcomes():
    steady = _samples(0, 1000, lambda i: 1000)       # 10 s of flat traffic
    assert cs._steadiness(steady, 0, 10 * S) == "steady"
    jumpy = _samples(0, 1000, lambda i: 1000 if i < 500 else 2000)
    assert cs._steadiness(jumpy, 0, 10 * S) == "not_steady"
    gap = [x for x in steady if not (200 <= int(x["t_start_ns"] / 10_000_000) < 400)]
    assert cs._steadiness(gap, 0, 10 * S) == "unknown"
