"""Capture, window buffering, overflow, redaction (design 39.5.2d sections 4, 6, 9)."""
import json

import pytest

from core.observability import errors, gate, redact


@pytest.fixture
def err_dir(tmp_path, monkeypatch):
    # isolate the host error directory and the gate state per test
    monkeypatch.setenv("ALEMS_ERROR_DIR", str(tmp_path))
    gate.reset_for_tests()
    yield tmp_path
    gate.force_exit()
    gate.reset_for_tests()


def _files(d):
    return sorted(d.glob("*/*.json"))


def _raise(exc):
    try:
        raise exc
    except Exception as e:  # noqa: BLE001
        return e


def test_outside_window_writes_immediately(err_dir):
    eid = errors.capture(_raise(TimeoutError("slow")), component="tests")
    assert eid and len(_files(err_dir)) == 1
    rec = errors.find(eid, err_dir)
    assert rec["error_code"] == "ALEMS-NET-0004"
    assert rec["captured_inside_window"] is False
    assert rec["traceback_hash"] and rec["exception_class"].endswith("TimeoutError")


def test_inside_window_buffers_until_exit(err_dir):
    gate.enter()
    eid = errors.capture(_raise(TimeoutError("slow")), component="tests")
    assert eid and _files(err_dir) == []  # no I/O inside [t0, t1]
    gate.exit()
    rec = errors.find(eid, err_dir)
    assert rec["captured_inside_window"] is True
    assert rec["error_code"] == "ALEMS-NET-0004"  # classified at flush


def test_overflow_keeps_id_and_sets_flag(err_dir, monkeypatch):
    monkeypatch.setattr(gate, "NON_LOSSY_CAPACITY", 0)
    gate.enter()
    eid = errors.capture(_raise(ValueError("x")))
    window = gate.exit()
    assert eid is not None  # allocated error_ref
    assert window["observability_overflow"] == 1
    assert errors.find(eid, err_dir) is None  # no record expected (section 6 rule 6)


def test_capture_never_raises(err_dir, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("context broken")
    monkeypatch.setattr(errors.context, "get_context", broken)
    before = gate.COUNTERS.get("capture_failed", 0)
    assert errors.capture(ValueError("x")) is None
    assert gate.COUNTERS["capture_failed"] == before + 1


def test_no_error_dir_counts_failure(monkeypatch):
    gate.reset_for_tests()
    monkeypatch.setattr(errors.locations, "error_dir", lambda: None)
    before = gate.COUNTERS.get("capture_failed", 0)
    errors.capture(ValueError("x"))
    assert gate.COUNTERS["capture_failed"] == before + 1


def test_redaction_env_and_patterns(err_dir, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    msg = "auth failed key sk-test-1234567890 url https://u:p@h/x?api_key=abc Bearer zzz.yyy"
    eid = errors.capture(_raise(RuntimeError(msg)))
    text = json.dumps(errors.find(eid, err_dir))
    for secret in ("sk-test-1234567890", "u:p@", "api_key=abc", "zzz.yyy"):
        assert secret not in text
    assert errors.find(eid, err_dir)["redactions"] >= 4


def test_redaction_failure_omits_text(err_dir, monkeypatch):
    def broken(text):
        raise RuntimeError("redactor broken")
    monkeypatch.setattr(redact, "redact", broken)
    rec = errors.find(errors.capture(_raise(RuntimeError("secret"))), err_dir)
    assert rec["message"] is None and rec["traceback_text"] is None and rec["redactions"] == -1


def test_unknown_site_code_falls_back(err_dir):
    rec = errors.find(errors.capture(_raise(ValueError("x")), code="ALEMS-PERS-9999"), err_dir)
    assert rec["error_code"] == "ALEMS-GEN-0000" and rec["declared_code"] == "ALEMS-PERS-9999"


def test_active_stage_receives_note(err_dir):
    class Rec:
        notes = []

        def note(self, stage_id, error_id, code):
            self.notes.append((stage_id, error_id, code))

    r = Rec()
    token = errors.push_stage(r, "persist_samples")
    try:
        eid = errors.capture(_raise(ValueError("x")), code="ALEMS-PERS-0105")
    finally:
        errors.pop_stage(token)
    assert r.notes == [("persist_samples", eid, "ALEMS-PERS-0105")]
    assert errors.find(eid, err_dir)["stage_id"] == "persist_samples"


def test_recoverable_override_only_lowers(err_dir):
    rec = errors.find(errors.capture(_raise(TimeoutError("t")), recoverable=False), err_dir)
    assert rec["recoverable"] is False


def test_capture_failure_still_notes_stage(err_dir, monkeypatch):
    class Rec:
        notes = []

        def note(self, stage_id, error_id, code):
            self.notes.append((stage_id, error_id, code))

    def broken(*a, **k):
        raise RuntimeError("context broken")
    monkeypatch.setattr(errors.context, "get_context", broken)
    r = Rec()
    token = errors.push_stage(r, "etl_hardware")
    try:
        assert errors.capture(ValueError("x"), code="ALEMS-ETL-0103") is None
    finally:
        errors.pop_stage(token)
    assert r.notes == [("etl_hardware", None, "ALEMS-ETL-0103")]


def test_capture_coded_returns_code_outside_window(err_dir):
    eid, code = errors.capture_coded(_raise(TimeoutError("t")), note=False)
    assert eid and code == "ALEMS-NET-0004"
