"""Tests for alems_plugin_bfcl.prepare (RAF LH 01 S4)."""

import pytest

from alems_plugin_bfcl.prepare import (
    CACHE_RESET_FAILED, RESTORE_MISMATCH, PreAttempt, reset_url)


class FakeEnv:
    def __init__(self, calls_made):
        self.calls_made = calls_made


class FakeProvider:
    """State is a label; restore operations change it deterministically."""

    def __init__(self, state="after_k", calls_made=5):
        self.state = state
        self.env = FakeEnv(calls_made)
        self.restored = []

    def state_hash(self):
        return "h_" + self.state

    def restore_initial(self):
        self.restored.append("initial")
        self.state = "initial"

    def restore_before(self, k):
        self.restored.append(("before", k))
        self.state = "before_%d" % k


class FakeSession:
    def __init__(self, failure, provider):
        self.failure = failure
        self.provider = provider
        self.initial_hash = "h_initial"
        self.prepared_restore = None


class FakeExecutor:
    pass


class Poster:
    def __init__(self, status=200, raise_exc=None):
        self.urls, self.status, self.raise_exc = [], status, raise_exc

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.raise_exc:
            raise self.raise_exc
        return self.status


def _failure(k_minus_1=3):
    return {"provider_calls": k_minus_1, "state_hash": "h_before_%d" % (k_minus_1 + 1)}


def _run(policy, poster=None, failure=True):
    ex = FakeExecutor()
    poster = poster or Poster()
    hook = PreAttempt(ex, "http://localhost:8000/v1", policy, post=poster)
    hook(1)
    ex._bfcl_session = FakeSession(_failure() if failure else None, FakeProvider())
    rec2 = hook(2)
    return ex, poster, rec2


def test_reset_url_strips_v1():
    assert reset_url("http://h:8000/v1/") == "http://h:8000/reset_prefix_cache"
    assert reset_url("http://h:8000") == "http://h:8000/reset_prefix_cache"


@pytest.mark.parametrize("policy,resets", [
    ("FR_warm", 1), ("SR_warm", 1), ("FR_cold", 2), ("SR_cold", 2)])
def test_reset_counts(policy, resets):
    _, poster, _ = _run(policy)
    assert len(poster.urls) == resets


def test_b0_resets_once_and_never_restores():
    ex, poster = FakeExecutor(), Poster()
    rec = PreAttempt(ex, "http://x/v1", "B0", post=poster)(1)
    assert len(poster.urls) == 1 and rec["restore"] is None
    assert ex._current_attempt == 1


def test_attempt_identity_set():
    ex, _, _ = _run("FR_warm")
    assert ex._current_attempt == 2
    assert set(ex._raf_prepare) == {1, 2}


def test_fr_restores_initial():
    ex, _, rec = _run("FR_cold")
    r = rec["restore"]
    assert r["ok"] and r["state_hash_after"] == "h_initial"
    assert ex._bfcl_session.prepared_restore is r
    assert rec["validity"] == []


def test_sr_restores_before_k():
    ex, _, rec = _run("SR_warm")
    assert ex._bfcl_session.provider.restored == [("before", 4)]
    assert rec["restore"]["ok"]


def test_sr_skips_restore_when_k_not_reached():
    ex = FakeExecutor()
    hook = PreAttempt(ex, "http://x/v1", "SR_warm", post=Poster())
    hook(1)
    prov = FakeProvider(state="before_4", calls_made=3)
    ex._bfcl_session = FakeSession(_failure(), prov)
    rec = hook(2)
    assert prov.restored == [] and rec["restore"]["ok"]


def test_reset_failure_status_recorded_not_raised():
    _, _, rec = _run("FR_cold", poster=Poster(status=404))
    assert CACHE_RESET_FAILED in rec["validity"]


def test_reset_exception_recorded_not_raised():
    _, _, rec = _run("SR_cold", poster=Poster(raise_exc=OSError("refused")))
    assert CACHE_RESET_FAILED in rec["validity"]
    assert "refused" in rec["resets"][0]["error"]


def test_restore_mismatch_flagged():
    ex = FakeExecutor()
    hook = PreAttempt(ex, "http://x/v1", "SR_warm", post=Poster())
    hook(1)
    f = _failure()
    f["state_hash"] = "h_other"
    ex._bfcl_session = FakeSession(f, FakeProvider())
    assert RESTORE_MISMATCH in hook(2)["validity"]


def test_no_failure_point_skips_restore():
    _, _, rec = _run("FR_warm", failure=False)
    assert rec["restore"] == {"skipped": "no_failure"}


def test_policy_written_to_context():
    ex = FakeExecutor()
    ex._raf_context = {"goal_key": "g1", "seed": 0}
    PreAttempt(ex, "http://x/v1", "SR_cold", post=Poster())
    assert ex._raf_context == {"goal_key": "g1", "seed": 0,
                               "policy": "SR", "cache": "cold"}


def test_unknown_policy_rejected():
    with pytest.raises(ValueError):
        PreAttempt(FakeExecutor(), "http://x/v1", "XX")
