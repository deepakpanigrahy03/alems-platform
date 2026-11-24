"""Tests for the kernel traffic pass (no GPU): parsing, failures, eligibility, atomic storage."""
import sqlite3
import subprocess
import types
from pathlib import Path

import pytest

from alems_plugin_pravaha import kernel_pass as kp

R, W, WQ, DF = ("lts__d_sectors_fill_sysmem.sum", "lts__t_sectors_aperture_sysmem_op_write.sum",
                "lts__t_requests_aperture_sysmem_op_write.sum", "lts__d_sectors_fill_device.sum")
MIB64 = 64 * 2 ** 20
SQL = Path(__file__).resolve().parents[1] / "migrations/extensions/energy_validation/e006_create_kernel_traffic.sql"


def _csv(kernels):
    """ncu --csv rows for {(id, name): {metric: value}}, with program output mixed in."""
    lines = ['"ID","Kernel Name","Metric Name","Metric Unit","Metric Value"']
    for (kid, name), d in kernels.items():
        for m, v in d.items():
            lines.append('"%s","%s","%s","sector","%s"' % (kid, name, m, v))
    return "program says hello\n" + "\n".join(lines) + "\n"


def _fake(monkeypatch, stdout, rc=0, stderr="", timeout=False):
    import csv as _c

    def parse(lines):
        body = [l for l in lines if l.startswith('"')]
        order, vals = [], {}
        for row in _c.DictReader(body):
            k = (row["ID"], row["Kernel Name"])
            if k not in vals:
                order.append(k)
                vals[k] = {}
            try:
                vals[k][row["Metric Name"]] = float(row["Metric Value"])
            except ValueError:
                pass
        return order, vals
    pk = types.SimpleNamespace(READ=R, WRITE=W, WRITE_REQ=WQ, DEVICE_FILL=DF, parse=parse,
                               find_ncu=lambda: "/x/ncu")
    monkeypatch.setattr(kp, "_metrics", lambda: (pk, [R, W, WQ, DF]))
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        if timeout:
            raise subprocess.TimeoutExpired(args, 1)
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(kp.subprocess, "run", run)
    return seen


# fill writes 64 MiB; multiply reads 64 MiB and writes 64 MiB (the GN100 fixture)
FIXTURE = {("0", "fill"): {R: 0, W: MIB64 // 32, WQ: 1, DF: 0},
           ("1", "mul"): {R: MIB64 // 32, W: MIB64 // 32, WQ: 1, DF: 0}}


def test_exact_bytes_per_kernel(monkeypatch):
    _fake(monkeypatch, _csv(FIXTURE))
    r = kp.run_ncu(["prog"], kp.settings())
    assert r["status"] == "completed"
    fill, mul = r["kernels"]
    assert (fill["read_bytes"], fill["write_bytes"]) == (0, MIB64)
    assert (mul["read_bytes"], mul["write_bytes"]) == (MIB64, MIB64)
    assert (r["read_bytes"], r["write_bytes"]) == (MIB64, 2 * MIB64)


def test_failures_never_zero(monkeypatch):
    _fake(monkeypatch, "", timeout=True)
    assert kp.run_ncu(["p"], kp.settings())["reason"] == "timeout"
    _fake(monkeypatch, _csv(FIXTURE), rc=3)
    assert kp.run_ncu(["p"], kp.settings())["reason"] == "workload_failed"
    _fake(monkeypatch, "nothing")
    assert kp.run_ncu(["p"], kp.settings())["reason"] == "no_kernel_data"
    _fake(monkeypatch, _csv({("0", "k"): {R: "n/a", W: 10}}))
    assert kp.run_ncu(["p"], kp.settings())["reason"] == "counter_unavailable"


def test_settings_layers_and_args(monkeypatch):
    monkeypatch.setenv("ALEMS_KTRAFFIC_CACHE_CONTROL", "none")
    monkeypatch.setenv("ALEMS_KTRAFFIC_TOL", "0.1")
    s = kp.settings({"match": "mul", "launch_count": 2})
    assert s["cache_control"] == "none" and s["tol"] == 0.1
    seen = _fake(monkeypatch, _csv(FIXTURE))
    kp.run_ncu(["p"], s)
    a = seen["args"]
    assert a[a.index("--cache-control") + 1] == "none" and "regex:mul" in a and a[a.index("-c") + 1] == "2"


def _ref(status="measured", samples=10, rd=MIB64, wr=2 * MIB64, device="0"):
    return {"status": status, "device": device, "samples": samples, "read_bytes": rd, "write_bytes": wr}


def test_eligibility():
    p = {"read_bytes": MIB64, "write_bytes": 2 * MIB64}
    s = kp.settings()
    assert kp.agreement(p, None, s, "0")["agreement_status"] == "no_reference"
    assert kp.agreement(p, _ref(status="failed"), s, "0")["agreement_status"] == "insufficient_coverage"
    sub = kp.agreement(p, _ref(), kp.settings({"match": "mul"}), "0")
    assert sub["agreement_status"] == "scope_mismatch" and sub["read_ratio"] is None
    assert kp.agreement(p, _ref(device="1"), s, "0")["agreement_status"] == "device_mismatch"
    z = kp.agreement(p, _ref(rd=0), s, "0")
    assert z["agreement_status"] == "zero_reference" and z["ref_read_bytes"] == 0 and z["read_ratio"] is None
    ok = kp.agreement(p, _ref(), s, "0")
    assert ok["agreement_status"] == "within" and ok["read_ratio"] == 1.0
    assert kp.agreement(p, _ref(rd=MIB64 // 2), s, "0")["agreement_status"] == "outside"


def _db():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.executescript("CREATE TABLE runs(run_id INTEGER PRIMARY KEY);"
                    "CREATE TABLE energy_validation_seq_attempts(attempt_id INTEGER PRIMARY KEY);"
                    "CREATE TABLE energy_validation_gpu_mem_traffic_runs(run_id, status, device, sample_count);"
                    "CREATE TABLE energy_validation_gpu_mem_traffic_samples(run_id, kernel_read_miss_bytes,"
                    " kernel_write_bytes);")
    c.executescript(SQL.read_text())
    c.execute("INSERT INTO runs VALUES (7)")
    c.execute("INSERT INTO energy_validation_gpu_mem_traffic_runs VALUES (7,'measured','0',2)")
    c.executemany("INSERT INTO energy_validation_gpu_mem_traffic_samples VALUES (7,?,?)",
                  [(MIB64 // 2, MIB64), (MIB64 // 2, MIB64)])
    return c


def _passing_probe(monkeypatch):
    monkeypatch.setattr(kp, "probe", lambda: {"feasibility": "pass", "reason": None, "ncu_path": "/x/ncu",
                                              "ncu_version": "v", "driver_profiling_flag": "0"})


def test_partial_match_keeps_kernels_with_scope_mismatch(monkeypatch):
    """Profile only the multiply against a whole run reference: completed, counts kept, ratio NULL."""
    c = _db()
    _passing_probe(monkeypatch)
    _fake(monkeypatch, _csv({("1", "mul"): FIXTURE[("1", "mul")]}))
    row = kp.profile(c, ["prog"], {"match": "mul"}, matched_run_id=7)
    assert row["status"] == "completed" and row["agreement_status"] == "scope_mismatch"
    assert (row["read_bytes"], row["write_bytes"]) == (MIB64, MIB64) and row["read_ratio"] is None
    db = c.execute("SELECT matched_run_id, read_ratio FROM energy_validation_kernel_traffic_passes").fetchone()
    assert db == (7, None)
    assert c.execute("SELECT COUNT(*) FROM energy_validation_kernel_traffic_kernels").fetchone()[0] == 1


def test_whole_run_within_and_failed_pass_stored(monkeypatch):
    c = _db()
    _passing_probe(monkeypatch)
    _fake(monkeypatch, _csv(FIXTURE))
    assert kp.profile(c, ["prog"], matched_run_id=7)["agreement_status"] == "within"
    monkeypatch.setattr(kp, "probe", lambda: {"feasibility": "fail", "reason": "no_permission", "ncu_path": "/x",
                                              "ncu_version": None, "driver_profiling_flag": "1"})
    row = kp.profile(c, ["prog"], matched_run_id=7)
    assert row["status"] == "failed" and row["reason"] == "infeasible" and row.get("read_bytes") is None
    st = c.execute("SELECT status, read_bytes, agreement_status FROM energy_validation_kernel_traffic_passes "
                   "WHERE pass_id=?", (row["pass_id"],)).fetchone()
    assert st == ("failed", None, "not_evaluated")


def test_store_is_atomic(monkeypatch):
    c = _db()
    with pytest.raises(sqlite3.IntegrityError):
        kp.store_pass(c, {"workload_json": "{}", "workload_sha256": "x", "feasibility": "pass", "metric_set": "m",
                          "cache_control": "all", "replay_mode": "kernel", "status": "completed",
                          "read_bytes": 1, "write_bytes": 1, "agreement_status": "no_reference",
                          "method_id": kp.METHOD_ID, "started_utc_ns": 1, "ended_utc_ns": 2},
                      [{"seq": 0, "read_sectors": 1, "write_sectors": 1, "read_bytes": 32, "write_bytes": 32},
                       {"seq": 0, "read_sectors": 1, "write_sectors": 1, "read_bytes": 32, "write_bytes": 32}])
    assert c.execute("SELECT COUNT(*) FROM energy_validation_kernel_traffic_passes").fetchone()[0] == 0
