"""
Tests for core/serving/engine_provenance.py (45.2b).

Two local HTTP servers serve "the same model" on two ports; each gets its own
provenance JSON. Each port must map to its own instance by PID and start time.
Also: secrets redacted, no worker found gives status unknown (never confirmed),
remote URL gives no row, PID reuse with a wrong start time does not match.
"""
import json
import socket
import subprocess
import sys
import time

import psutil
import pytest

from core.serving import engine_provenance as ep


def _free_port():
    """A free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port):
    """Start a plain HTTP server (no children) and wait until it listens."""
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                             "--bind", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if ep.find_listener(port):
            return proc
        time.sleep(0.1)
    proc.kill()
    pytest.skip("listener did not come up")


def _record(tmp_path, proc, port, start_shift=0.0, name="m"):
    """Write a v2 provenance JSON for proc, optionally with a wrong start time."""
    start = psutil.Process(proc.pid).create_time() + start_shift
    rec = {"schema": ep.PROVENANCE_SCHEMA, "engine": "vllm", "engine_version": "0.23.0",
           "pid": proc.pid, "port": port, "model_id": "mistralai/Mistral-7B",
           "process_start_utc": ep._utc_iso(start),
           "command": ["vllm", "serve", "--api-key", "<redacted>"]}
    path = tmp_path / ("serve_%s_%d.json" % (name, port))
    path.write_text(json.dumps(rec))
    return path


@pytest.fixture
def two_servers(tmp_path):
    """Two listeners on two ports, one provenance JSON each."""
    ports = [_free_port(), _free_port()]
    procs = [_serve(p) for p in ports]
    paths = [_record(tmp_path, pr, po) for pr, po in zip(procs, ports)]
    ep._CACHE.clear()
    yield ports, procs, paths
    for pr in procs:
        pr.terminate()
        pr.wait()


def test_two_servers_map_to_own_instance(two_servers, tmp_path):
    ports, procs, paths = two_servers
    rows = [ep.match_instance("http://localhost:%d/v1" % p, str(tmp_path)) for p in ports]
    assert [r["server_pid"] for r in rows] == [p.pid for p in procs]
    assert [r["source_path"] for r in rows] == [str(p) for p in paths]
    assert all(r["match_method"] == "pid_start_time" for r in rows)
    assert rows[0]["server_instance_id"] != rows[1]["server_instance_id"]


def test_no_worker_is_unknown_never_confirmed(two_servers, tmp_path):
    ports, _, _ = two_servers
    row = ep.match_instance("http://127.0.0.1:%d/v1" % ports[0], str(tmp_path))
    assert row["listener_identified"] == 1
    assert row["worker_association_status"] == "unknown"
    assert row["worker_identified"] == 0


def test_wrong_start_time_does_not_match(tmp_path):
    port = _free_port()
    proc = _serve(port)
    try:
        _record(tmp_path, proc, port, start_shift=30.0)
        ep._CACHE.clear()
        row = ep.match_instance("http://localhost:%d" % port, str(tmp_path))
        assert row["match_method"] == "listener_only_no_record"
        assert row["engine_version"] is None and row["source_path"] is None
    finally:
        proc.terminate()
        proc.wait()


def test_no_listener_row_and_remote_none(tmp_path):
    row = ep.match_instance("http://localhost:%d/v1" % _free_port(), str(tmp_path))
    assert row["match_method"] == "no_listener" and row["server_pid"] is None
    assert ep.match_instance("http://100.84.85.2:8000/v1", str(tmp_path)) is None
    assert ep.match_instance(None, str(tmp_path)) is None


def test_redaction():
    out = ep.redact(["x", "--api-key", "s3cret", "--hf-token=hf_abc",
                     "hf_ABCDEFGHIJKLMNOPQRST", "--port", "8000"])
    assert out == ["x", "--api-key", "<redacted>", "--hf-token=<redacted>",
                   "<redacted>", "--port", "8000"]
