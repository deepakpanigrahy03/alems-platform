"""
SPEC 39.5.2 section 6 acceptance on the real persistence path.

A failure inside span building (core.vocabularies.agent.span_builder._build)
must yield: an error record on disk coded ALEMS-SPAN-0001, the spans stage
failed with error_ref and reason swallowed:ALEMS-SPAN-0001, and unchanged
behaviour (the run still commits and returns its run_id).

Only the storage steps are stubbed (as in test_run_persistence_stages.py);
RunPersistenceService, the span stage, span_builder, StageRecorder, the
gate, the catalog, and the error store are all real.
"""
import contextlib
import json

import pytest

from core.execution import run_persistence as rp
from core.observability import gate
from core.observability import stages as st
from core.vocabularies.agent import span_builder


class _DB(object):
    """Minimal adapter: transaction() that commits or propagates."""

    @contextlib.contextmanager
    def transaction(self):
        yield


@pytest.fixture
def svc(monkeypatch, tmp_path):
    """Service with storage steps stubbed; span building and capture real."""
    monkeypatch.setenv("ALEMS_ERROR_DIR", str(tmp_path))
    gate.reset_for_tests()
    s = rp.RunPersistenceService()
    monkeypatch.setattr(s, "_insert_run_row", lambda *a: 7)
    for name in ("_insert_samples", "_insert_events", "_insert_after_commit",
                 "_apply_duration_fix", "_compute_residual"):
        monkeypatch.setattr(s, name, lambda *a: None)
    for name in ("compute_phase_attribution", "aggregate_hardware_metrics",
                 "populate_tool_failure_wasted_energy", "compute_energy_attribution",
                 "populate_ttft_tpot"):
        monkeypatch.setattr(rp, name, lambda *a: None)
    yield s, tmp_path
    gate.reset_for_tests()


def _result():
    return {"ml_features": {}}


def test_forced_span_failure(svc, monkeypatch):
    """Mirrors save_single after 2d part D: build in spans part 1, flush hook in part 2."""
    s, err_dir = svc
    called = []

    def boom(*a, **k):
        called.append(True)
        raise KeyError("forced span failure")

    monkeypatch.setattr(span_builder, "_build", boom)
    r = st.StageRecorder("save_single")
    # part 1: the real build call, wrapped exactly as experiment_runner does
    with st.stage_or_noop(r, "spans"):
        span_builder.build_spans_from_result(None, "root", _result(), "linear", {})
    # part 2: persistence with a flush hook that succeeds
    run_id = s.insert_one_run(_DB(), 1, 1, _result(), "linear", 1,
                              after_run_row=lambda rid: None, stages=r)

    # unchanged behaviour: span failure swallowed, run commits, id returned
    assert called and run_id == 7
    ev = r.events["spans"]
    assert ev["status"] == "failed"  # worst part wins over the successful flush
    assert ev["reason"] == "swallowed:ALEMS-SPAN-0001"
    assert ev["error_ref"]
    files = list(err_dir.glob("*/%s.json" % ev["error_ref"]))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["error_code"] == "ALEMS-SPAN-0001"
    assert rec["stage_id"] == "spans"
    assert rec["exception_class"].endswith("KeyError")
    assert r.events["persist_run"]["status"] == "succeeded"
