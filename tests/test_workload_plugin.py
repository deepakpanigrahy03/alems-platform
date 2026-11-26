"""
Tests for alems-plugin-workload against a fake OpenAI compatible server
(no GPU, no model). The fake counts one token per word plus a fixed chat
template overhead, streams content chunks, and can be told to reject
ignore_eos, stop early, omit usage or fail.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from alems_sdk.sequencer import StepFailed

from alems_plugin_workload import client, prompts
from alems_plugin_workload.matmul_step import MatmulLoadStep
from alems_plugin_workload.outcome import eos_flag, request_results
from alems_plugin_workload.request_step import RequestProfileStep

TEMPLATE_TOKENS = 7  # tokens the fake chat template adds around the user text


class Fake(object):
    """Behaviour switches of the fake server, reset per test."""
    def __init__(self):
        self.ignore_eos_ok, self.tokenize_ok, self.usage = True, True, True
        self.stop_after, self.fail, self.cached, self.stop_reason = None, False, 0, None
        self.bodies = []


FAKE = Fake()


def _count(body):
    text = body["messages"][0]["content"]
    return len(text.split()) + TEMPLATE_TOKENS


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FAKE.bodies.append((self.path, body))
        if self.path == "/tokenize":
            return self._json(200, {"count": _count(body)}) if FAKE.tokenize_ok else self._json(404, {})
        if self.path == "/reset_prefix_cache":
            return self._json(200, {})
        if body.get("ignore_eos") and not FAKE.ignore_eos_ok:
            return self._json(400, {"error": "unknown field"})
        if FAKE.fail:
            return self._json(500, {"error": "boom"})
        if not body.get("stream"):
            return self._json(200, {"usage": {"prompt_tokens": _count(body), "completion_tokens": 1}})
        self._stream(body)

    def _stream(self, body):
        mx = body["max_tokens"]
        k = min(mx, FAKE.stop_after) if FAKE.stop_after else mx
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for _ in range(k):
            self._event({"choices": [{"delta": {"content": "w "}, "finish_reason": None}]})
        fin = {"delta": {}, "finish_reason": "length" if k >= mx else "stop", "stop_reason": FAKE.stop_reason}
        self._event({"choices": [fin]})
        if FAKE.usage:
            self._event({"choices": [], "usage": {"prompt_tokens": _count(body), "completion_tokens": k,
                                                  "prompt_tokens_details": {"cached_tokens": FAKE.cached}}})
        self.wfile.write(b"data: [DONE]\n\n")

    def _event(self, ev):
        self.wfile.write(("data: %s\n\n" % json.dumps(ev)).encode())
        self.wfile.flush()


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(autouse=True)
def reset_fake():
    FAKE.__init__()


def params(endpoint, **kw):
    p = {"kind": "request_profile", "profile": "BASE", "profile_version": "workload_conditions/1",
         "prompt_tokens": 120, "output_tokens": 16, "n_requests": 3, "endpoint": endpoint,
         "model": "m", "seed": 45, "tolerance_pct": 5, "sizing_tolerance_pct": 2,
         "ignore_eos": "auto", "timeout_s": 10, "reset_prefix_cache": "auto"}
    p.update(kw)
    return p


def run_step(p):
    step = RequestProfileStep()
    ctx = {"attempt_id": 1, "cancelled": lambda: False, "prepared": step.prepare(p, {})}
    avail = step.is_available(p, ctx)
    if not avail.ok:
        return avail, None
    step.setup(p, ctx)
    return avail, step.measure(p, ctx)


# ---- prompts ---------------------------------------------------------------

def test_prompts_reproducible_and_unique():
    a = prompts.make_prompt(45, 1, 0, 50)
    assert a == prompts.make_prompt(45, 1, 0, 50)
    others = {prompts.make_prompt(45, 1, i, 50)[:20] for i in range(20)}
    assert len(others) == 20                           # unique marker per request
    assert prompts.make_prompt(45, 2, 0, 50) != a      # new round, new prompts


def test_size_prompt_converges():
    count = lambda t: len(t.split()) + TEMPLATE_TOKENS
    text, n, its = prompts.size_prompt(lambda w: prompts.make_prompt(1, 1, 0, w), count, 4000, 2, 8)
    assert prompts.within(n, 4000, 2) == 1 and its <= 8


# ---- request_profile against the fake server -------------------------------

def test_full_length_with_ignore_eos(server):
    avail, res = run_step(params(server))
    assert avail.ok
    assert res["share_output_within_tol"][0] == 1.0 and res["share_prompt_within_tol"][0] == 1.0
    assert res["cap_ignore_eos_used"][0] == 1 and res["cap_tokenize"][0] == 1
    assert res["req_000.finish_reason"][0] == "length" and res["req_000.early_eos"][0] == 0
    assert res["req_002.output_tokens"] == (16, "tokens")
    assert res["cached_share"][0] == 0.0 and res["n_cached_reported"][0] == 3
    streamed = [b for path, b in FAKE.bodies if path.endswith("completions") and b.get("stream")]
    assert all(b["ignore_eos"] and b["temperature"] == 0 for b in streamed)


def test_early_stop_is_not_called_eos(server):
    FAKE.stop_after = 5
    _, res = run_step(params(server))
    assert res["n_early_stop"][0] == 3
    assert res["n_early_eos_identified"][0] == 0
    assert "req_000.early_eos" not in res           # cause unknown: stored NULL
    assert res["share_output_within_tol"][0] == 0.0


def test_eos_identified_only_when_server_names_it(server):
    FAKE.stop_after, FAKE.stop_reason = 5, 2
    _, res = run_step(params(server, eos_token_ids=[2]))
    assert res["req_000.early_eos"][0] == 1 and res["req_000.stop_reason"][0] == 2


def test_missing_usage_is_null_not_zero(server):
    FAKE.usage = False
    _, res = run_step(params(server))
    assert "req_000.output_tokens" not in res and "req_000.cached_tokens" not in res
    assert "output_tokens_total" not in res and "cached_share" not in res


def test_ignore_eos_unsupported(server):
    FAKE.ignore_eos_ok = False
    avail, _ = run_step(params(server, ignore_eos="true"))
    assert not avail.ok and avail.code == "ignore_eos_unsupported"
    avail, res = run_step(params(server, ignore_eos="auto"))
    assert avail.ok and res["cap_ignore_eos_used"][0] == 0


def test_sizing_falls_back_to_probe(server):
    FAKE.tokenize_ok = False
    _, res = run_step(params(server))
    assert res["cap_tokenize"][0] == 0 and res["share_prompt_within_tol"][0] == 1.0


def test_failed_request_fails_step(server):
    p = params(server)
    step = RequestProfileStep()
    ctx = {"attempt_id": 1, "cancelled": lambda: False, "prepared": step.prepare(p, {})}
    FAKE.fail = True
    with pytest.raises(StepFailed) as exc:
        step.measure(p, ctx)
    assert exc.value.code == "request_failed"


def test_unreachable_endpoint_reports_prepare_failed():
    avail, _ = run_step(params("http://127.0.0.1:9", timeout_s=1))
    assert not avail.ok and avail.code == "prepare_failed"


def test_validate_params():
    assert RequestProfileStep.validate(params("http://x"))  == []
    errs = RequestProfileStep.validate({"profile": "BASE", "ignore_eos": "maybe"})
    assert any("ignore_eos" in e for e in errs) and any("prompt_tokens" in e for e in errs)


# ---- pure helpers -----------------------------------------------------------

def test_eos_flag_rules():
    assert eos_flag("length", None, 16, 16, []) == 0
    assert eos_flag("stop", None, 5, 16, []) is None
    assert eos_flag("stop", 2, 5, 16, [2]) == 1
    assert eos_flag(None, None, None, 16, []) is None


def test_request_results_drop_none():
    res = request_results([{"output_tokens": 3, "cached_tokens": None}])
    assert res == {"req_000.output_tokens": (3, "tokens")}


def test_parse_sse_noise():
    assert client.parse_sse(b": keepalive") is None
    assert client.parse_sse(b"data: [DONE]") is None
    assert client.parse_sse(b"data: {bad") is None


# ---- matmul_load ------------------------------------------------------------

def mm(**kw):
    p = {"duration_s": 1, "python": "/nonexistent/python", "script": "scripts/tools/matmul_load.py",
         "n": 2048, "dtype": "bfloat16", "duty": 0.15, "period_ms": 100, "seed": 45,
         "max_start_temp_c": 70, "abort_temp_c": 80, "check_interval_s": 1, "attended": True}
    p.update(kw)
    return p


def test_matmul_attended_only():
    a = MatmulLoadStep().is_available(mm(attended=False), {})
    assert not a.ok and a.code == "attended_only"
    assert MatmulLoadStep.validate(mm(attended=False))


def test_matmul_python_missing():
    a = MatmulLoadStep().is_available(mm(), {})
    assert not a.ok and a.code == "python_missing"


def test_matmul_summary_parse():
    r = MatmulLoadStep._results("matmul_load n=2048 dtype=bfloat16 iters=10 busy_s=1.50 wall_s=10.00 duty=0.150", 1, 2)
    assert r["achieved_duty"] == (0.15, None) and r["iters"] == (10, None)
    with pytest.raises(StepFailed):
        MatmulLoadStep._results("garbage", 1, 2)
