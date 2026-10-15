"""RB restore in prepare (R2 spec 2.1): index arithmetic and validity."""

from types import SimpleNamespace

from alems_plugin_bfcl.prepare import PreAttempt, RB_NOT_APPLICABLE


class _Env:
    """Fake env: state is the call log length (enough for RB arithmetic)."""

    def __init__(self, calls):
        self.call_log = ["c%d" % i for i in range(calls)]
        self.restored_k = None

    @property
    def calls_made(self):
        return len(self.call_log)

    def restore_before(self, k):
        # State before call k keeps calls 1 .. k-1.
        self.restored_k = k
        self.call_log = self.call_log[:k - 1]


class _Prov:
    def __init__(self, calls, turn_starts):
        self.env = _Env(calls)
        self.turn_starts = list(turn_starts)

    def restore_before(self, k):
        self.env.restore_before(k)
        self.turn_starts = [s for s in self.turn_starts if s <= k - 1]

    def state_hash(self):
        return "h%d" % self.env.calls_made


def _hook(calls, turn_starts, turn):
    prov = _Prov(calls, turn_starts)
    f = {"turn": turn, "turn_starts": list(turn_starts), "kind": "tool",
         "provider_calls": calls, "state_hash": "x"}
    session = SimpleNamespace(provider=prov, failure=f, initial_hash="h0")
    ex = SimpleNamespace(_bfcl_session=session)
    hook = PreAttempt(ex, "http://x/v1", "RB_warm", post=lambda u, t: 200)
    return hook, prov


def test_rb_restores_to_turn_start():
    hook, prov = _hook(calls=7, turn_starts=[0, 3, 5], turn=2)
    rec = hook(2)
    assert prov.env.restored_k == 6
    assert prov.env.calls_made == 5
    assert prov.turn_starts == [0, 3]
    assert rec["restore"]["ok"] and rec["validity"] == []


def test_rb_turn_zero_not_applicable_not_invalid():
    hook, prov = _hook(calls=2, turn_starts=[0], turn=0)
    rec = hook(2)
    assert prov.env.calls_made == 0 and prov.turn_starts == []
    assert rec["validity"] == [RB_NOT_APPLICABLE]


def test_rb_no_calls_in_suspect_turn_leaves_state():
    hook, prov = _hook(calls=3, turn_starts=[0, 3], turn=1)
    rec = hook(2)
    assert prov.env.restored_k is None and prov.env.calls_made == 3
    assert prov.turn_starts == [0] and rec["restore"]["ok"]


def test_rb_policy_context():
    hook, _ = _hook(calls=1, turn_starts=[0], turn=0)
    assert hook.executor._raf_context["policy"] == "RB"
    assert hook.executor._raf_context["cache"] == "warm"
