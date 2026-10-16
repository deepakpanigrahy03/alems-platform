"""
R6 C1 to C3: a pre attempt hook that abandons stops the goal before t0.

Attempt 1 runs (fake harness), attempt 2 is abandoned by the hook: the
harness is never called again, attempt 2 is finished as a failure with
failure_type "abandoned", only attempt 1 is persisted, and the goal closes
as failed. A hook that does not abandon keeps the old behaviour.
"""

from types import SimpleNamespace

import pytest

import core.execution.goal_execution_manager as gem


class Tracker:
    def __init__(self):
        self.finished = []
        self.goal = None
        self._next = 10

    def start_goal(self, **kw):
        return 1

    def start_attempt(self, **kw):
        self._next += 1
        return self._next

    def finish_attempt(self, **kw):
        self.finished.append(kw)

    def finish_goal(self, **kw):
        self.goal = kw


class Harness:
    def __init__(self):
        self.calls = 0

    def run_agentic(self, **kw):
        self.calls += 1
        return {"execution": {"execution": {"status": "failure"}}}


class Retry:
    def should_retry(self, *a, **k):
        return {"action": "retry", "reason": "test"}


def _hook(abandon_at):
    def hook(attempt):
        return {"abandon": True} if attempt == abandon_at else {"attempt": attempt}
    return hook


@pytest.fixture
def run(monkeypatch):
    persisted = []

    def persist(db, conn, exp_id, hw_id, goal_id, wt, rep, task, measured, derived=True):
        persisted.extend(m["attempt_num"] for m in measured)
        return None, [], []

    monkeypatch.setattr(gem, "_persist_attempts", persist)
    monkeypatch.setattr(gem.goal_execution_etl, "process_one", lambda *a, **k: None)
    monkeypatch.setattr(gem, "_record_attempt_failure", lambda *a, **k: None)
    monkeypatch.setattr(gem._failure_classifier, "classify", lambda **k: "timeout")
    import core.execution.experiment_runner as er
    monkeypatch.setattr(er, "_run_quality_scoring", lambda **k: None)

    def go(abandon_at, attempts=3):
        tr, h = Tracker(), Harness()
        ex = SimpleNamespace(config={"is_local": True}, pre_attempt_hook=_hook(abandon_at))
        writer = SimpleNamespace(conn=object())
        gem._execute_goal_impl(
            None, 1, 1, h, ex, {"id": "t", "prompt": "p"}, "agentic", 1, tr,
            SimpleNamespace(max_retries=attempts - 1, backoff_seconds=0),
            retry_adapter=Retry(), writer=writer)
        return tr, h, persisted
    return go


def test_abandon_at_attempt_2_stops_goal(run):
    tr, h, persisted = run(abandon_at=2)
    assert h.calls == 1                               # no harness call after abandon
    assert [f["failure_type"] for f in tr.finished] == ["timeout", "abandoned"]
    assert tr.finished[1]["outcome"] == "failure"
    assert persisted == [1]                           # abandoned attempt: no window, no run
    assert tr.goal["success"] is False


def test_no_abandon_keeps_old_behaviour(run):
    tr, h, persisted = run(abandon_at=99)
    assert h.calls == 3 and persisted == [1, 2, 3]
    assert "abandoned" not in [f["failure_type"] for f in tr.finished]
