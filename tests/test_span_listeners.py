"""Tests for span listeners (45.4d) and the NVTX agreement checker, no GPU or nsys needed."""
import importlib.util
import os
import sqlite3
import sys
import types

from core.observability import span_listeners as sl


class _Rec:
    def __init__(self, sid, kind="phase", name="inference", parent=None):
        self.span_id, self.trace_id, self.parent_span_id = sid, "t", parent
        self.kind, self.name, self.start_ns, self.end_ns, self.status = kind, name, 1, None, "open"


class _Listener:
    def __init__(self):
        self.events = []

    def on_open(self, span):
        self.events.append(("open", span["span_id"]))

    def on_close(self, span):
        self.events.append(("close", span["span_id"]))


def test_events_forwarded_and_failing_listener_dropped(monkeypatch):
    good, bad = _Listener(), _Listener()
    bad.on_open = lambda span: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(sl, "_LISTENERS", [bad, good])
    sl.on_open(_Rec("a"))
    sl.on_close(_Rec("a"))
    assert good.events == [("open", "a"), ("close", "a")] and sl._LISTENERS == [good]


def test_no_listener_is_noop(monkeypatch):
    monkeypatch.setattr(sl, "_LISTENERS", [])
    sl.on_open(_Rec("a"))   # nothing to do, nothing raised


def test_nvtx_listener_enabled_only_with_config(monkeypatch):
    from alems_plugin_nvtx.listener import NvtxSpanListener
    fake = types.ModuleType("nvtx")
    calls = []
    fake.start_range = lambda message, domain: calls.append(("start", message, domain)) or 7
    fake.end_range = lambda h: calls.append(("end", h))
    monkeypatch.setitem(sys.modules, "nvtx", fake)
    off = NvtxSpanListener()
    off.configure(sl.resolve_config("nvtx", {"enabled": False, "domain": "alems"}, env={}))
    assert off.enabled() is False
    lis = NvtxSpanListener()
    lis.configure(sl.resolve_config("nvtx", {"enabled": False, "domain": "alems"}, env={"ALEMS_NVTX_ENABLED": "1"}))
    assert lis.enabled()
    lis.on_open({"span_id": "abcd1234", "kind": "phase", "name": "tool"})
    lis.on_close({"span_id": "abcd1234"})
    assert calls == [("start", "phase:tool [abcd1234]", "alems"), ("end", 7)]


def _checker():
    path = os.environ.get("ALEMS_TEST_NVTX_CHECKER", "scripts/tools/check_nvtx_spans.py")
    spec = importlib.util.spec_from_file_location("check_nvtx_spans", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _files(tmp_path, shift_ns):
    store, rep = str(tmp_path / "s.db"), str(tmp_path / "r.sqlite")
    c = sqlite3.connect(store)
    c.execute("CREATE TABLE spans(span_id, kind, name, start_ns, end_ns, parent_span_id, run_id)")
    c.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?,1)",
                  [("aaaaaaaa1", "run", "r", 1000, 9_000_000, None),
                   ("bbbbbbbb2", "phase", "inference", 2_000_000, 5_000_000, "aaaaaaaa1")])
    c.commit()
    r = sqlite3.connect(rep)
    r.execute("CREATE TABLE NVTX_EVENTS(start, end, text, textId)")
    base = 50_000_000_000   # different clock zero point
    r.executemany("INSERT INTO NVTX_EVENTS VALUES (?,?,?,NULL)",
                  [(base + 1000, base + 9_000_000, "run:r [aaaaaaaa1]"),
                   (base + 2_000_000 + shift_ns, base + 5_000_000 + shift_ns, "phase:inference [bbbbbbbb2]")])
    r.commit()
    return store, rep


def test_checker_agrees_and_detects_shift(tmp_path):
    ck = _checker()
    store, rep = _files(tmp_path, 0)
    assert ck.main(["--nsys", rep, "--store", store, "--run-id", "1", "--tol-ms", "0.5"]) == 0
    shifted = tmp_path / "shifted"
    shifted.mkdir()
    store, rep = _files(shifted, 2_000_000)   # inference range starts 2 ms late
    assert ck.main(["--nsys", rep, "--store", store, "--run-id", "1", "--tol-ms", "0.5"]) == 1


def test_resolve_config_layers():
    base = {"enabled": False, "domain": "alems", "depth": 3}
    assert sl.resolve_config("nvtx", base, env={}) == base
    r = sl.resolve_config("nvtx", base, env={"ALEMS_NVTX_ENABLED": "true", "ALEMS_NVTX_DOMAIN": "p5", "ALEMS_NVTX_DEPTH": "5"})
    assert r == {"enabled": True, "domain": "p5", "depth": 5}


def test_checker_writes_row_and_reports_missing_trace(tmp_path):
    ck = _checker()
    store, rep = _files(tmp_path, 0)
    sql = os.environ.get("ALEMS_TEST_E007", "migrations/extensions/energy_validation/e007_create_nvtx_agreement.sql")
    c = sqlite3.connect(store)
    c.execute("CREATE TABLE runs(run_id INTEGER PRIMARY KEY)")
    c.executescript(open(sql).read())
    c.commit()
    assert ck.main(["--nsys", rep, "--store", store, "--run-id", "1", "--tol-ms", "0.5", "--write"]) == 0
    assert ck.main(["--nsys", str(tmp_path / "none.sqlite"), "--store", store, "--run-id", "1", "--write"]) == 1
    rows = sqlite3.connect(store).execute("SELECT status, matched, spans_stored FROM energy_validation_nvtx_agreement "
                                          "ORDER BY check_id").fetchall()
    assert rows == [("agree", 2, 2), ("trace_missing", 0, 2)]
