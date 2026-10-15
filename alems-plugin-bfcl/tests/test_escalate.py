"""Escalating arm in PreAttempt (R2 8.1): policy switch before attempt 3."""

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


def _hook(policy, escalate=None):
    prov = _Prov()
    f = {"turn": 1, "turn_starts": [0, 3], "kind": "llm", "provider_calls": 4,
         "state_hash": "s4"}
    session = SimpleNamespace(provider=prov, failure=f, initial_hash="s0")
    ex = SimpleNamespace(_bfcl_session=session)
    posts = []
    hook = PreAttempt(ex, "http://x/v1", policy,
                      post=lambda u, t: posts.append(u) or 200, escalate=escalate)
    return hook, prov, posts


def test_fixed_arm_keeps_policy_on_attempt_3():
    hook, prov, posts = _hook("SR_warm")
    hook(1)
    hook(2)
    rec = hook(3)
    assert rec["policy"] == "SR_warm" and rec["restore"]["depth"] == "SR"
    assert len(posts) == 1                     # warm: only the attempt 1 reset


def test_escalating_arm_switches_on_attempt_3():
    seen = []

    def esc(attempt, recs):
        seen.append((attempt, sorted(recs)))
        return "FR_cold"

    hook, prov, posts = _hook("SR_warm", escalate=esc)
    hook(1)
    assert hook(2)["policy"] == "SR_warm"
    rec = hook(3)
    assert seen == [(3, [1, 2])]
    assert rec["policy"] == "FR_cold" and rec["restore"]["depth"] == "FR"
    assert prov.restores[-1] == "initial" and len(posts) == 2   # cold reset
    assert hook.executor._raf_context["policy"] == "FR"
    assert hook.executor._raf_context["cache"] == "cold"


def test_escalation_none_keeps_policy():
    hook, _, _ = _hook("SR_warm", escalate=lambda a, r: None)
    hook(1)
    hook(2)
    assert hook(3)["policy"] == "SR_warm"


def test_escalation_rejects_unknown_policy():
    hook, _, _ = _hook("SR_warm", escalate=lambda a, r: "XX_hot")
    hook(1)
    hook(2)
    with pytest.raises(ValueError):
        hook(3)
