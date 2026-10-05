"""
StageRecorder with C-ERR capture (design 39.5.2d sections 7.1 to 7.5).

Uses the same gate stubbing as test_stages.py so no record reaches disk;
the real StageRecorder, stage graphs, catalog, and classifier are used.
"""
import pytest

from core.observability import errors as oe
from core.observability import stage_graphs as sg
from core.observability import stages as st


@pytest.fixture(autouse=True)
def fake_gate(monkeypatch):
    """Replace gate calls so tests do not depend on window state or disk."""
    seq = iter(range(1, 10000))
    sent = []
    monkeypatch.setattr(st.gate, "next_seq", lambda: next(seq))
    monkeypatch.setattr(st.gate, "submit", lambda k, r: sent.append((k, r)))
    monkeypatch.setattr(st.gate, "is_inside", lambda: False)
    return sent


def _swallow(exc, code):
    """What a v1 except body now does: log (omitted here) and capture."""
    try:
        raise exc
    except Exception as e:  # noqa: BLE001  the site swallows, as in v1
        oe.capture(e, code=code, component="tests")


def test_graphs_declare_partial_ok_and_version():
    for gid in sg.GRAPHS:
        assert sg.GRAPHS[gid]["graph_version"] == "1.2.0"
        flags = {n["stage_id"]: n["partial_ok"] for n in sg.GRAPHS[gid]["nodes"]}
        assert flags["persist_samples"] and flags["etl_hardware"] and flags["etl_phase"]
        assert not flags["spans"]


def test_exception_records_code_and_error_ref():
    r = st.StageRecorder("save_single")
    with pytest.raises(TimeoutError):
        with r.stage("spans"):
            raise TimeoutError("slow")
    ev = r.events["spans"]
    assert ev["status"] == "failed"
    assert ev["reason"] == "ALEMS-NET-0004"
    assert ev["error_ref"]


def test_swallowed_in_partial_ok_stage_is_partial():
    r = st.StageRecorder("save_single")
    with r.stage("persist_samples"):
        _swallow(ValueError("bad batch"), "ALEMS-PERS-0105")
    ev = r.events["persist_samples"]
    assert (ev["status"], ev["outcome"]) == ("succeeded", "partial")
    assert ev["reason"] == "swallowed:ALEMS-PERS-0105"
    assert ev["error_ref"]


def test_swallowed_in_strict_stage_fails():
    r = st.StageRecorder("save_single")
    with r.stage("spans"):
        _swallow(KeyError("k"), "ALEMS-SPAN-0001")
    ev = r.events["spans"]
    assert ev["status"] == "failed" and ev["reason"] == "swallowed:ALEMS-SPAN-0001"


def test_several_swallowed_first_ref_all_codes():
    r = st.StageRecorder("save_single")
    with r.stage("etl_hardware"):
        _swallow(ValueError("a"), "ALEMS-ETL-0101")
        _swallow(ValueError("b"), "ALEMS-ETL-0103")
    ev = r.events["etl_hardware"]
    assert ev["reason"] == "swallowed:ALEMS-ETL-0101,ALEMS-ETL-0103"
    assert ev["outcome"] == "partial" and ev["error_ref"]


def test_capture_failure_marked(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("context broken")
    monkeypatch.setattr(oe.context, "get_context", broken)
    r = st.StageRecorder("save_single")
    with r.stage("persist_samples"):
        _swallow(ValueError("x"), "ALEMS-PERS-0101")
    ev = r.events["persist_samples"]
    assert ev["reason"] == "swallowed:ALEMS-PERS-0101;capture_failed"
    assert ev["error_ref"] is None


def test_no_stage_leak_after_exit():
    r = st.StageRecorder("save_single")
    with r.stage("persist_samples"):
        pass
    assert oe._ACTIVE_STAGE.get() is None
