"""retry_context step kind (Paper 5, P5-C) against a fake chat server (no GPU, no model)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from alems_plugin_workload import retry_step as rs

TEMPLATE = 5   # tokens the fake chat template adds per message


class Fake(object):
    def __init__(self):
        self.calls, self.resets, self.fail_on, self.queries, self.hits = [], 0, None, 0, 0


FAKE = Fake()


def _count(messages):
    return sum(len(m["content"].split()) + TEMPLATE for m in messages)


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

    def do_GET(self):
        if self.path == "/metrics":
            raw = ("vllm:prefix_cache_queries_total{m=\"x\"} %d\nvllm:prefix_cache_hits_total{m=\"x\"} %d\n"
                   % (FAKE.queries, FAKE.hits)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/tokenize":
            return self._json(200, {"count": _count(body["messages"])})
        if self.path == "/reset_prefix_cache":
            FAKE.resets += 1
            return self._json(200, {})
        FAKE.calls.append(body)
        if FAKE.fail_on is not None and len(FAKE.calls) == FAKE.fail_on:
            return self._json(500, {"error": "boom"})
        n_in, n_out = _count(body["messages"]), int(body["max_tokens"])
        FAKE.queries += n_in
        FAKE.hits += n_in // 2
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for _ in range(n_out):
            ev = {"choices": [{"delta": {"content": "word "}, "finish_reason": None}]}
            self.wfile.write(("data: %s\n\n" % json.dumps(ev)).encode())
        last = {"choices": [{"delta": {}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": n_in, "completion_tokens": n_out}}
        self.wfile.write(("data: %s\n\ndata: [DONE]\n\n" % json.dumps(last)).encode())


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


@pytest.fixture(autouse=True)
def fresh():
    global FAKE
    FAKE.__init__()


def params(endpoint, arm, k, **kw):
    p = {"arm": arm, "k": k, "n_prompts": 3, "prompt_tokens": 100, "output_tokens": 20,
         "retry_note": "The previous answer was rejected. Try again.", "endpoint": endpoint,
         "model": "m", "seed": 7, "tolerance_pct": 5, "sizing_tolerance_pct": 5, "ignore_eos": "true",
         "reset_prefix_cache": "auto", "timeout_s": 10, "max_sizing_iter": 12,
         "prefix_cache_queries_metric": "vllm:prefix_cache_queries_total",
         "prefix_cache_hits_metric": "vllm:prefix_cache_hits_total"}
    p.update(kw)
    return p


def run(step):
    kind = rs.RetryContextStep()
    ctx = {"cancelled": lambda: False}
    ctx["prepared"] = kind.prepare(step, ctx)
    assert kind.is_available(step, ctx).ok, ctx["prepared"]
    return kind.measure(step, ctx)


def test_validate_arm_and_k():
    v = rs.RetryContextStep.validate
    assert any("baseline needs k 0" in e for e in v({"arm": "baseline", "k": 1}))
    assert any("growth needs k" in e for e in v({"arm": "growth", "k": 0}))


def test_prompts_identical_across_arms_and_k(server):
    seen = {}
    for arm, k in (("baseline", 0), ("fixed", 2), ("growth", 3)):
        prep = rs.RetryContextStep().prepare(params(server, arm, k), {})
        seen[(arm, k)] = sorted((p["index"], p["text"]) for p in prep["prompts"])
    assert seen[("baseline", 0)] == seen[("fixed", 2)] == seen[("growth", 3)]


def test_fixed_resends_base_only(server):
    res = run(params(server, "fixed", 2))
    assert res["n_goals"][0] == 3 and res["attempts_per_goal"][0] == 3
    assert len(FAKE.calls) == 9 and all(len(c["messages"]) == 1 for c in FAKE.calls)
    assert res["g_000.a_1.outcome"][0] == "failed_injected" and res["g_000.a_3.outcome"][0] == "success"
    assert FAKE.resets == 3


def test_growth_appends_answer_and_restated_task(server):
    run(params(server, "growth", 2, n_prompts=1))
    lens = [len(c["messages"]) for c in FAKE.calls]
    assert lens == [1, 3, 5]
    last = FAKE.calls[-1]["messages"]
    assert last[1]["role"] == "assistant" and last[1]["content"].startswith("word")
    assert last[2]["content"].startswith("The previous answer was rejected.") and last[0]["content"] in last[2]["content"]
    ins = [_count(c["messages"]) for c in FAKE.calls]
    assert ins[1] - ins[0] == pytest.approx(ins[2] - ins[1], rel=0.05)   # constant growth per retry


def test_final_single_one_call_shaped_like_growth_last(server):
    res = run(params(server, "final_single", 2, n_prompts=1))
    assert len(FAKE.calls) == 1 and len(FAKE.calls[0]["messages"]) == 5
    assert res["g_000.a_1.outcome"][0] == "success"


def test_goal_window_tokens_and_cache_deltas(server):
    res = run(params(server, "growth", 1, n_prompts=1))
    assert res["g_000.t1_ns"][0] > res["g_000.t0_ns"][0]
    assert res["g_000.input_tokens_total"][0] == res["g_000.a_1.prompt_tokens"][0] + res["g_000.a_2.prompt_tokens"][0]
    assert res["g_000.a_1.prefix_cache_queried_tokens"][0] == res["g_000.a_1.prompt_tokens"][0]
    assert res["g_000.a_2.prefix_cache_hit_share"][0] == pytest.approx(0.5, abs=0.01)


def test_no_metrics_named_gives_no_cache_rows(server):
    res = run(params(server, "baseline", 0, n_prompts=1, prefix_cache_queries_metric=None))
    assert not any("prefix_cache" in key for key in res)


def test_failed_call_fails_step(server):
    from alems_sdk.sequencer import StepFailed
    FAKE.fail_on = 2
    with pytest.raises(StepFailed):
        run(params(server, "fixed", 1, n_prompts=2))
