"""
S1b unit tests for the bfcl_react adapter (no model server needed).

A scripted client replays BFCL ground truth: in turn t it issues the
ground truth calls of turn t one per response, then answers in text. It
derives its position from the message history alone, so it behaves
correctly after full restart and shallow resume.
"""

import inspect
import json
from types import SimpleNamespace

import pytest

from alems_plugin_bfcl import react, tasks
from alems_plugin_bfcl.env import BFCLEnv, parse_call
from alems_plugin_bfcl.react import BFCLReactAdapter, ERROR_NOTICE
from alems_plugin_bfcl.scorer import BFCLStateScorer

TID = "multi_turn_base_0"      # 4 turns, 10 ground truth calls


# ── fakes ────────────────────────────────────────────────────────────
def _as_tool_call(task_id, call, n):
    """Ground truth string -> OpenAI tool call with keyword arguments."""
    name, args, kwargs = parse_call(call)
    if args:
        method = BFCLEnv(tasks.load_entry(task_id)).method_map()[name]
        params = list(inspect.signature(method).parameters)
        kwargs = dict(zip(params, args), **kwargs)
    return {"id": "c%08d" % n, "type": "function",
            "function": {"name": name, "arguments": json.dumps(kwargs)}}


class ScriptedClient:
    """Replays ground truth; counts requests; optional endless tool calls."""

    def __init__(self, task_id, loop_forever=False):
        self.task_id = task_id
        self.gt = tasks.ground_truth(task_id)
        self.loop_forever = loop_forever
        self.requests = []

    def complete(self, payload):
        msgs = payload["messages"]
        self.requests.append(msgs)
        turn = sum(1 for m in msgs if m["role"] == "user") - 1
        last_user = max(i for i, m in enumerate(msgs) if m["role"] == "user")
        done = sum(1 for m in msgs[last_user:] if m["role"] == "tool")
        cached = 0 if len(self.requests) == 1 else 50
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
                 "prompt_tokens_details": {"cached_tokens": cached}}
        if self.loop_forever:
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [_as_tool_call(self.task_id, "ls()", len(self.requests))]}
        elif done < len(self.gt[turn]):
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [_as_tool_call(self.task_id, self.gt[turn][done],
                                                len(self.requests))]}
        else:
            msg = {"role": "assistant", "content": "Done with turn %d." % turn}
        return {"choices": [{"message": msg}], "usage": usage}


class OnceInjector:
    """Fires cls once at (phase, ordinal); records every check."""

    def __init__(self, cls, phase, ordinal):
        self.cls, self.phase, self.ordinal = cls, phase, ordinal
        self.checks, self.fired = [], 0

    def is_active(self):
        return True

    def should_inject(self, step_index, phase, tool_name, draw_number, attempt_id, goal_id):
        self.checks.append((phase, step_index))
        if self.fired == 0 and phase == self.phase and step_index == self.ordinal:
            self.fired += 1
            return True, self.cls
        return False, None


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Record sleeps instead of waiting."""
    slept = []
    monkeypatch.setattr(react, "_sleep", slept.append)
    return slept


def _executor(injector=None, policy="FR", attempt=1, goal_key="g1"):
    return SimpleNamespace(
        config={"model_id": "fake", "base_url": "http://x/v1"},
        failure_injector=injector, _current_attempt=attempt, _current_attempt_id=attempt,
        _raf_context={"policy": policy, "goal_key": goal_key, "iteration_limit": 40})


def _run(adapter, executor):
    cfg = {"model_config": executor.config, "task": "", "tool_graph": None,
           "executor": executor, "task_id": "bfcl_" + TID}
    return adapter.execute_task(cfg, tools=[], engine=None)


def _adapter(client):
    a = BFCLReactAdapter()
    a.client_factory = lambda url: client
    return a


def _score(meta):
    return BFCLStateScorer().score(meta["raf"]["scorer_output"], TID)[0]


def _two_attempts(cls, phase, ordinal, policy):
    """Attempt 1 with injection, attempt 2 with recovery; returns both metas."""
    client = ScriptedClient(TID)
    a = _adapter(client)
    inj = OnceInjector(cls, phase, ordinal)
    ex = _executor(inj, policy, attempt=1)
    m1 = _run(a, ex).metadata
    ex._current_attempt, ex._current_attempt_id = 2, 2
    n_checks = len(inj.checks)
    m2 = _run(a, ex).metadata
    return m1, m2, inj, n_checks, client


# ── B0 ───────────────────────────────────────────────────────────────
def test_b0_completes_and_scores():
    """No injector: all turns run; the state scorer passes."""
    r = _run(_adapter(ScriptedClient(TID)), _executor())
    m = r.metadata
    assert r.success and m["execution"]["status"] == "success"
    assert m["tool_count"] == 10 and m["llm_calls"] == 14     # 10 calls + 4 turn ends
    assert _score(m) == 1.0


def test_metadata_has_every_harness_key():
    """Keys harness and execute_goal read from the executor result."""
    m = _run(_adapter(ScriptedClient(TID)), _executor()).metadata
    for k in ("response", "execution", "tokens", "llm_calls", "tool_count", "tools_used",
              "steps", "pending_interactions", "phase_times", "phase_ratios",
              "complexity_score", "orchestration_cpu_ms", "events", "api_latency_ms",
              "compute_time_ms", "total_time_ms", "avg_step_time_ms"):
        assert k in m, k
    assert set(m["tokens"]) == {"prompt", "completion", "total"}
    assert len(m["pending_interactions"]) == m["llm_calls"]
    assert m["raf"]["llm_calls"][1]["cached_tokens"] == 50


# ── injection, all classes, both policies ────────────────────────────
CASES = [("tool_error", "tool", 2), ("timeout", "tool", 5),
         ("rate_limit", "llm", 2), ("json_parse", "llm", 7)]


@pytest.mark.parametrize("cls,phase,k", CASES)
@pytest.mark.parametrize("policy", ["FR", "SR"])
def test_fire_once_then_recover(cls, phase, k, policy):
    """Attempt 1 fails at k with cls; attempt 2 never consults the injector."""
    m1, m2, inj, n_checks, _ = _two_attempts(cls, phase, k, policy)
    assert m1["execution"]["status"] == "failure" and m1["execution"]["error_type"] == cls
    i1 = m1["raf"]["injection"]
    assert i1["fired"] == 1 and i1["ordinal"] == k and i1["call_type"] == phase
    assert inj.fired == 1 and len(inj.checks) == n_checks       # no checks in attempt 2
    assert m2["raf"]["injection"]["fired"] == 0
    assert m2["execution"]["status"] == "success" and _score(m2) == 1.0


@pytest.mark.parametrize("cls,phase,k", CASES)
def test_restore_hashes(cls, phase, k):
    """FR restores the initial state; SR the state before the failed call."""
    for policy in ("FR", "SR"):
        m1, m2, *_ = _two_attempts(cls, phase, k, policy)
        rec = m2["raf"]["recovery"]
        assert rec["policy"] == policy
        assert rec["state_hash_after_restore"] == rec["expected_hash"]
        if policy == "FR":
            assert rec["expected_hash"] == m1["raf"]["initial_state_hash"]
        else:
            assert rec["expected_hash"] == m1["raf"]["injection"]["failure_point"]["state_hash"]


def test_sr_replays_less_than_fr():
    """SR resumes at the failure point; FR replays everything before it."""
    fr = _two_attempts("tool_error", "tool", 5, "FR")[1]
    sr = _two_attempts("tool_error", "tool", 5, "SR")[1]
    assert sr["tool_count"] < fr["tool_count"] and sr["llm_calls"] < fr["llm_calls"]
    assert fr["tool_count"] == 10


def test_sr_appends_notice():
    """SR adds the fixed notice to the end of the kept history."""
    _, _, _, _, client = _two_attempts("json_parse", "llm", 7, "SR")
    first_a2 = [r for r in client.requests if any(ERROR_NOTICE in (m.get("content") or "")
                                                   for m in r)]
    assert first_a2, "notice never sent"
    _, _, _, _, client = _two_attempts("tool_error", "tool", 5, "SR")
    assert any(ERROR_NOTICE in (m.get("content") or "") for r in client.requests for m in r)


# ── waits and limits ─────────────────────────────────────────────────
def test_rate_limit_waits_in_attempt_2(no_real_sleep):
    """retry_after 2 s is waited at the start of attempt 2, no request sent."""
    m1, m2, _, _, client = _two_attempts("rate_limit", "llm", 2, "SR")
    assert no_real_sleep == [react.RATE_LIMIT_RETRY_AFTER_S]
    assert m1["pending_interactions"][-1]["status"] == "failure"
    assert any(e["event_type"] == "waiting" for e in m2["events"])


def test_timeout_blocks_in_attempt_1(no_real_sleep):
    """timeout blocks 5 s inside attempt 1, then fails."""
    _two_attempts("timeout", "tool", 3, "FR")
    assert no_real_sleep == [react.TIMEOUT_BLOCK_S]


def test_iteration_limit():
    """An agent that never stops calling tools hits the limit."""
    ex = _executor()
    ex._raf_context["iteration_limit"] = 6
    m = _run(_adapter(ScriptedClient(TID, loop_forever=True)), ex).metadata
    assert m["execution"]["error_type"] == "iteration_limit" and m["llm_calls"] == 6


def test_model_error_is_a_failure_not_an_exception():
    """A real server error ends the attempt with api_error."""
    class Down:
        def complete(self, payload):
            raise react.ModelError("ConnectionRefusedError: down")
    m = _run(_adapter(Down()), _executor()).metadata
    assert m["execution"]["status"] == "failure" and m["execution"]["error_type"] == "api_error"


def test_new_goal_gets_fresh_session():
    """A new goal key starts from the initial state even at attempt 2."""
    a = _adapter(ScriptedClient(TID))
    ex = _executor(goal_key="g1")
    _run(a, ex)
    ex._raf_context["goal_key"], ex._current_attempt = "g2", 2
    m = _run(a, ex).metadata
    assert m["raf"]["goal_key"] == "g2" and _score(m) == 1.0


def test_client_bypasses_proxy_and_normalizes_url():
    c = react.ChatClient("http://localhost:8000/v1/chat/completions")
    assert c.url == "http://localhost:8000/v1/chat/completions"
    assert react.ChatClient("http://localhost:8000/v1/").url.endswith("/v1/chat/completions")
