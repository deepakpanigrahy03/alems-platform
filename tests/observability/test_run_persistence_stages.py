"""
WP 39.5.2c step 3a: stage mapping inside RunPersistenceService.

The service's inner steps are replaced by recorders of call order, so the
test checks only the stage instrumentation: correct stage per step, rollback
downgrade, multi part stages, and identical behaviour with stages=None.
"""
import contextlib

import pytest

from core.execution import run_persistence as rp
from core.observability import stages as st


class _DB(object):
    """Minimal adapter: transaction() that commits or propagates."""

    @contextlib.contextmanager
    def transaction(self):
        yield


@pytest.fixture
def svc(monkeypatch):
    """Service with every inner step stubbed; calls are logged in order."""
    calls = []
    s = rp.RunPersistenceService()
    monkeypatch.setattr(st.gate, "next_seq", lambda: len(calls))
    monkeypatch.setattr(st.gate, "submit", lambda k, r: None)
    monkeypatch.setattr(s, "_insert_run_row", lambda *a: calls.append("run") or 7)
    monkeypatch.setattr(s, "_insert_samples", lambda *a: calls.append("samples"))
    monkeypatch.setattr(s, "_insert_events", lambda *a: calls.append("events"))
    monkeypatch.setattr(s, "_insert_after_commit", lambda *a: calls.append("after"))
    monkeypatch.setattr(s, "_apply_duration_fix", lambda *a: calls.append("dur"))
    monkeypatch.setattr(s, "_compute_residual", lambda *a: calls.append("res"))
    for name in ("compute_phase_attribution", "aggregate_hardware_metrics",
                 "populate_tool_failure_wasted_energy", "compute_energy_attribution",
                 "populate_ttft_tpot"):
        monkeypatch.setattr(rp, name, lambda *a, _n=name: calls.append(_n))
    return s, calls


def _result():
    return {"ml_features": {}}


def test_no_recorder_same_call_order(svc):
    """stages=None: the original step order, nothing else."""
    s, calls = svc
    assert s.insert_one_run(_DB(), 1, 1, _result(), "linear", 1) == 7
    assert calls == ["run", "samples", "events", "after", "compute_phase_attribution",
                     "aggregate_hardware_metrics", "populate_tool_failure_wasted_energy",
                     "compute_energy_attribution", "populate_ttft_tpot", "dur", "res"]


def test_stage_mapping(svc):
    """Each step lands in its declared stage, all succeeded."""
    s, calls = svc
    r = st.StageRecorder("save_single")
    s.insert_one_run(_DB(), 1, 1, _result(), "linear", 1,
                     after_run_row=lambda rid: calls.append("hook"), stages=r)
    assert set(r.events) == {"persist_run", "spans", "persist_samples", "etl_hardware",
                             "etl_phase", "attribution", "residual"}
    assert all(e["status"] == "succeeded" for e in r.events.values())
    assert r.events["persist_run"]["counts"] == {"runs": 1}


def test_samples_failure_rolls_back_run(svc, monkeypatch):
    """persist_samples raising inside the transaction fails persist_run too."""
    s, calls = svc
    monkeypatch.setattr(s, "_insert_samples", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    r = st.StageRecorder("save_single")
    with pytest.raises(RuntimeError):
        s.persist_raw(_DB(), 1, 1, _result(), "linear", 1, stages=r)
    assert r.events["persist_run"]["reason"] == "rolled_back"
    assert r.events["persist_samples"]["status"] == "failed"
