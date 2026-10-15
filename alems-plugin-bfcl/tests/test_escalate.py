"""Decision seam in PreAttempt (R2 8.1, R6 addendum A): policy before attempts 2+."""

from types import SimpleNamespace

import pytest

from alems_plugin_bfcl.prepare import PreAttempt


class _Prov:
    """Fake provider: records restores; state is a label."""

    def __init__(self):
        self.env = SimpleNamespace(calls_made=5, call_log=[0] * 5)
        self.turn_starts = [0, 3]
        self.state = "s5"
        self.restores = []

    def restore_initial(self):
        self.restores.append("initial")
        self.state = "s0"

    def restore_before(self, k):
        self.restores.append(k)
        self.state = "s%d" % (k - 1)

    def state_hash(self):
        return self.state


def _hook(policy, decide=None):
    prov = _Prov()
    f = {"turn": 1, "turn_starts": [0, 3], "kind": "llm", "provider_calls": 4,
         "state_hash": "s4"}
    session = SimpleNamespace(provider=prov, failure=f, initial_hash="s0")
    ex = SimpleNamespace(_bfcl_session=session)
    posts = []
    hook = PreAttempt(ex, "http://x/v1", policy,
                      post=lambda u, t: posts.append(u) or 200, decide=decide)
    return hook, prov, posts


def _escalate(attempt, executor):
    # Same rule as the driver stub: keep at attempt 2, escalate at 3.
    return "FR_cold" if attempt >= 3 else None


def test_no_decider_keeps_policy():
    hook, _, posts = _hook("SR_warm")
    hook(1)
    hook(2)
    rec = hook(3)
    assert rec["policy"] == "SR_warm" and rec["restore"]["depth"] == "SR"
    assert len(posts) == 1                     # warm: only the attempt 1 reset


def test_escalating_arm_switches_on_attempt_3():
    hook, prov, posts = _hook("SR_warm", decide=_escalate)
    hook(1)
    assert hook(2)["policy"] == "SR_warm"
    rec = hook(3)
    assert rec["policy"] == "FR_cold" and rec["restore"]["depth"] == "FR"
    assert prov.restores[-1] == "initial" and len(posts) == 2   # cold reset
    ctx = hook.executor._raf_context
    assert ctx["policy"] == "FR" and ctx["cache"] == "cold"
    dec = hook.executor._raf_decisions
    assert dec[2]["chosen"] is None and dec[3]["chosen"] == "FR_cold"
    assert dec[3]["ns"] >= 0


def test_decider_sees_executor_and_decides_at_attempt_2():
    seen = []

    def decide(attempt, executor):
        seen.append((attempt, executor._bfcl_session.failure["kind"]))
        return "RB_warm"

    hook, _, _ = _hook("SR_warm", decide=decide)
    hook(1)
    rec = hook(2)
    assert seen == [(2, "llm")]
    assert rec["policy"] == "RB_warm" and rec["restore"]["depth"] == "RB"


def test_decider_never_called_on_attempt_1():
    calls = []
    hook, _, _ = _hook("SR_warm", decide=lambda a, e: calls.append(a))
    hook(1)
    assert calls == []


def test_unknown_policy_rejected():
    hook, _, _ = _hook("SR_warm", decide=lambda a, e: "XX_hot")
    hook(1)
    with pytest.raises(ValueError):
        hook(2)
