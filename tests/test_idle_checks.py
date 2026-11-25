"""Tests for the idle reference and recording check rules (synthetic data only)."""

import types

import pytest

from extensions.thermal_exposure import gpu_users as gu
from extensions.thermal_exposure import idle_checks as ic

CFG = dict(ic.DEFAULTS)
S = 1_000_000_000          # one second in ns


def _series(seconds, hz, value=lambda t: 50.0, start=0):
    """(times, values) at hz for the given seconds."""
    n = int(seconds * hz)
    ts = [start + int(i * S / hz) for i in range(n)]
    return ts, [value(t / S) for t in ts]


# ── medians and counters ────────────────────────────────────────────────────

def test_median_mad_and_missing_is_none():
    assert ic.median_mad([1.0, 2.0, 3.0, 100.0]) == (2.5, 1.0)
    assert ic.median_mad([]) == (None, None)


def test_counter_power_with_and_without_known_wrap():
    s = [(0, 1000.0), (S, 2000.0), (2 * S, 500.0)]          # wraps at the third read
    assert ic.interval_power_mw(s) == [pytest.approx(1.0)]   # 1000 uJ in 1 s = 1 mW
    got = ic.interval_power_mw(s, counter_range_uj=10_000.0)
    assert got == [pytest.approx(1.0), pytest.approx(8.5)]


# ── pre window acceptance ───────────────────────────────────────────────────

def test_cooling_trace_fails_slope_and_flat_trace_passes():
    ts, falling = _series(120, 20, lambda t: 60.0 - 0.01 * t)        # 0.6 C/min
    _, flat = _series(120, 20, lambda t: 50.0 + (0.001 if int(t) % 2 else 0.0))
    p = [12000.0] * len(ts)
    assert ic.assess_stability(ts, falling, 0.001, p, 1.0, CFG)["slope_ok"] is False
    assert ic.assess_stability(ts, flat, 0.001, p, 1.0, CFG)["slope_ok"] is True


def test_whole_degree_flat_trace_is_not_proof_of_stability():
    ts, flat = _series(120, 10, lambda t: 57.0)
    res = ic.assess_stability(ts, flat, 1.0, [12000.0] * len(ts), 1.0, CFG)
    assert res["slope_ok"] is False          # bound >= 1 C / 2 min = 0.5 C/min


def test_final_gap_fails_even_with_high_total_coverage():
    ts, _ = _series(600, 20)
    end = 600 * S
    holed = [t for t in ts if not (end - 10 * S <= t < end - 9 * S)]   # 1 s hole at the end
    res = ic.assess_series(holed, (0, end), 20, CFG)
    assert res["coverage"] > 0.99
    assert any("final gap" in r for r in res["reasons"])


def test_relative_mad_unknown_near_zero_power():
    ts, temps = _series(120, 20)
    res = ic.assess_stability(ts, temps, 0.001, [2.0] * len(ts), 1.0, CFG)
    assert res["power_ok"] is None


# ── cooling ─────────────────────────────────────────────────────────────────

def test_cooling_band_falls_back_to_resolution_when_mad_is_zero():
    cfg = dict(CFG, cool_tolerance_c=0.5)
    assert ic.cooling_band(0.0, 1.0, cfg) == 1.0
    assert ic.cooling_band(0.4, 0.001, cfg) == pytest.approx(1.2)


def test_undershoot_does_not_count_as_cooled():
    h = ic.CoolingHold({"z": (50.0, 2.0, 0.05)}, hold_s=1.0)
    done = [h.feed("z", i * S // 20, 45.0) for i in range(40)]   # 5 C below the band
    assert not any(done)


def test_gap_in_hold_resets_the_timer():
    h = ic.CoolingHold({"z": (50.0, 2.0, 0.05)}, hold_s=1.0)
    for i in range(15):
        h.feed("z", i * S // 20, 50.5)                     # 0.7 s in band
    t = 15 * S // 20 + 2 * S                               # 2 s hole
    assert h.feed("z", t, 50.5) is False
    assert h.resets == 1
    for i in range(1, 25):
        last = h.feed("z", t + i * S // 20, 50.5)
    assert last is True                                    # 1.2 s clean after the hole


def test_guard_sensor_out_of_band_blocks_completion():
    h = ic.CoolingHold({"z": (50.0, 2.0, 0.05), "gpu": (57.0, 2.0, 0.5)}, hold_s=1.0)
    for i in range(60):
        h.feed("z", i * S // 20, 50.0)
        if i % 10 == 0:
            h.feed("gpu", i * S // 20, 62.0)              # GPU still hot
    assert not h.complete(3 * S)


# ── drift ───────────────────────────────────────────────────────────────────

def test_drift_flags_and_missing_post_is_unknown():
    assert ic.drift(50.0, 51.0, 0.5) == ("warn", 1.0)
    assert ic.drift(50.0, 50.2, 0.5)[0] == "pass"
    assert ic.drift(50.0, None, 0.5) == ("unknown", None)
    assert ic.drift(10000.0, 10600.0, 0.05, relative=True)[0] == "warn"


# ── other GPU users and idle state ──────────────────────────────────────────

def _proc(pid, name, util):
    return {"pid": pid, "name": name, "mem_bytes": 1, "util": util}


def test_second_gpu_process_with_activity_fails():
    snaps = [[_proc(10, "llama", 50), _proc(99, "python", 30)]]
    res = gu.classify(snaps, own={10})
    assert res["activity"] == "fail" and res["external"] == [99]


def test_idle_context_only_warns_and_no_visibility_is_unknown():
    assert gu.classify([[_proc(99, "x", 0)]], own=set())["process"] == "warn"
    assert gu.classify([[_proc(99, "x", 0)]], own=set())["activity"] == "pass"
    assert gu.classify([None, None], own=set())["activity"] == "unknown"
    assert gu.classify([[_proc(99, "x", None)]], own=set())["activity"] == "unknown"


def test_snapshot_without_nvml_is_none():
    assert gu.snapshot(None) is None
    broken = types.SimpleNamespace(nvmlDeviceGetHandleByIndex=lambda i: 1 / 0)
    assert gu.snapshot(broken) is None


def test_idle_state_pid_change_fails_and_stopped_passes():
    a = [[_proc(5, "vllm", 0)], [_proc(5, "vllm", 0)]]
    b = [[_proc(5, "vllm", 0)], [_proc(6, "vllm", 0)]]
    assert gu.verify_idle_state("server_loaded", a, ["vllm"])["result"] == "pass"
    assert gu.verify_idle_state("server_loaded", b, ["vllm"])["result"] == "fail"
    assert gu.verify_idle_state("server_loaded", [[]], ["vllm"])["result"] == "fail"
    assert gu.verify_idle_state("server_stopped", [[]], ["vllm"])["result"] == "pass"
    assert gu.verify_idle_state(None, a, ["vllm"])["result"] == "unknown"
