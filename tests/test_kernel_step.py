"""Tests for kernel_rw, the shared window helpers and session characterisation (no GPU)."""
import json
import sqlite3

import pytest

from alems_plugin_pravaha import kernel_characterize as kc
from alems_plugin_pravaha import kernel_step as ks
from alems_plugin_pravaha import window as w
from alems_sdk.sequencer import StepFailed

S = 1_000_000_000
MIB16 = 16 * 2 ** 20


def _run(t0=10 * S, t1=70 * S, rc=0, ok=True, logical=MIB16 * 100):
    return {"returncode": rc, "t0": t0, "t1": t1, "first_t1": t0 + S // 10, "stderr_tail": "",
            "attrs_before": {"managed_memory": 1}, "attrs_after": {"managed_memory": 1},
            "check": {"ok": ok, "checksum": 7, "logical_bytes": logical, "aux_write_bytes": 1536 * 100,
                      "arch": "compute_121", "runtime_version": 13000, "blocks": 192, "threads": 256}}


def test_validate(monkeypatch):
    monkeypatch.setenv("ALEMS_KRW_SIZES_MIB", "16,256")
    v = ks.KernelRWStep.validate
    assert v({"placement": "device", "op": "read", "size_mib": 16}) == []
    assert any("managed" in e for e in v({"placement": "managed", "op": "read", "size_mib": 16}))
    assert any("prefetch" in e for e in v({"placement": "pinned", "op": "read", "size_mib": 16, "prefetch": "device"}))
    assert any("size_mib" in e for e in v({"placement": "device", "op": "read", "size_mib": 64}))


def test_prepare_sizes_times(monkeypatch):
    monkeypatch.setattr(ks, "run_kernel", lambda step, n, t, hold_s=None: _run(0, int(0.8 * S)))
    prep = ks.KernelRWStep().prepare({"placement": "device", "op": "read", "size_mib": 16, "duration_s": 60}, {})
    assert prep["probe_pass_s"] == pytest.approx(0.1) and prep["times"] == 600


def test_measure_results_control_and_failures(monkeypatch):
    step = {"placement": "managed", "op": "read", "size_mib": 16, "duration_s": 60, "prefetch": "none"}
    prep = {"times": 100, "probe_pass_s": 0.6, "duration_s": 60}
    monkeypatch.setattr(ks, "run_kernel", lambda *a, **k: _run())
    r = ks.KernelRWStep().measure(step, {"prepared": prep})
    assert r["logical_bytes"][0] == MIB16 * 100 and r["prefetch"][0] == "none" and r["duration_ok"][0]
    assert r["residency_changed"][0] is None and json.loads(r["memory_attributes_before"][0])["managed_memory"] == 1
    seen = {}

    def held(step, times, timeout, hold_s=None):
        seen.update(times=times, hold=hold_s)
        return _run(logical=0)
    monkeypatch.setattr(ks, "run_kernel", held)
    c = ks.KernelRWStep().measure(dict(step, control=True), {"prepared": {"times": 0, "duration_s": 60}})
    assert seen == {"times": 0, "hold": 60} and c["logical_bytes"][0] == 0 and c["achieved_logical_rate_bps"][0] is None
    monkeypatch.setattr(ks, "run_kernel", lambda *a, **k: _run(rc=3, ok=False))
    with pytest.raises(StepFailed) as e:
        ks.KernelRWStep().measure(step, {"prepared": prep})
    assert e.value.code == "check_failed"


def _samples(n, read, write):
    return [{"t_start_ns": i * 10_000_000, "t_end_ns": (i + 1) * 10_000_000,
             "kernel_read_miss_bytes": read, "kernel_write_bytes": write} for i in range(n)]


def test_evaluate_per_logical_and_zero_traffic():
    step = {"placement": "device", "op": "read", "size_mib": 16}
    results = {"work_t0_ns": (0, "ns"), "work_t1_ns": (10 * S, "ns"), "logical_bytes": (1000 * 1000, "B"),
               "control": (False, None)}
    run = {"traffic": [{"status": "measured", "samples": _samples(1000, 100, 5)}]}
    out = ks.KernelRWStep().evaluate(step, {}, results, run)
    assert out["observed_read_miss_bytes"][0] == pytest.approx(100_000)
    assert out["observed_per_logical_byte"][0] == pytest.approx(0.1) and out["traffic_steadiness"][0] == "steady"
    zero = {"traffic": [{"status": "measured", "samples": _samples(1000, 0, 0)}]}
    assert ks.KernelRWStep().evaluate(step, {}, results, zero)["traffic_steadiness"][0] == "unknown"
    assert ks.KernelRWStep().evaluate(step, {}, results, {})["observed_per_logical_byte"][0] is None


def test_window_helpers():
    s = [{"t_start_ns": 0, "t_end_ns": 10, "x": 100}, {"t_start_ns": 10, "t_end_ns": 20, "x": None}]
    assert w.bytes_in(s, 5, 20, "x") == pytest.approx(50.0)
    assert w.bytes_in(s, 10, 20, "x") is None


def test_characterize_first_completed_treatment():
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE energy_validation_seq_steps(step_id, session_id, kind, role, condition, params_json, planned_index);"
        "CREATE TABLE energy_validation_seq_attempts(attempt_id, step_id, execution_status);"
        "CREATE TABLE energy_validation_seq_attempt_runs(attempt_id, run_id, child_seq);"
        "CREATE TABLE energy_validation_seq_step_results(attempt_id, key, value);")
    p = json.dumps({"placement": "device", "op": "read", "size_mib": 16})
    pc = json.dumps({"placement": "device", "op": "read", "size_mib": 16, "control": True})
    rows = [(1, 1, "kernel_rw", "treatment", "dev_read", p, 3, "failed", 11),
            (2, 1, "kernel_rw", "treatment", "dev_read", p, 7, "completed", 12),
            (3, 1, "kernel_rw", "treatment", "dev_read", p, 9, "completed", 13),
            (4, 1, "kernel_rw", "treatment", "dev_ctl", pc, 5, "completed", 14)]
    for sid, sess, kind, role, cond, pj, idx, st, run in rows:
        c.execute("INSERT INTO energy_validation_seq_steps VALUES (?,?,?,?,?,?,?)", (sid, sess, kind, role, cond, pj, idx))
        c.execute("INSERT INTO energy_validation_seq_attempts VALUES (?,?,?)", (sid, sid, st))
        c.execute("INSERT INTO energy_validation_seq_attempt_runs VALUES (?,?,0)", (sid, run))
        c.execute("INSERT INTO energy_validation_seq_step_results VALUES (?, 'times', '600')", (sid,))
    t = kc.treatment_runs(c, 1)
    assert [x["run_id"] for x in t] == [12] and t[0]["treatment_times"] == "600"
    cmd = kc.command({"placement": "managed", "op": "write", "size_mib": 16, "prefetch": "none"}, 2)
    assert cmd[-8:] == ["managed", "write", "2", "16", "--init", "gpu", "--prefetch", "none"]
