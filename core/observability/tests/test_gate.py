"""Tests for core.observability.gate (39.5.2 WP 2b)."""

import builtins
import io
import logging
import threading

import pytest

from core.observability import gate


@pytest.fixture(autouse=True)
def _clean():
    """Fresh gate per test; restore caller lookup no matter what."""
    saved = logging._srcfile
    gate.reset_for_tests()
    yield
    gate.reset_for_tests()
    logging._srcfile = saved


def _sink():
    """A StreamHandler on a StringIO, wrapped for the gate, on a private logger."""
    stream = io.StringIO()
    target = logging.StreamHandler(stream)
    target.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("alems.test.gate.%d" % id(stream))
    lg.handlers[:] = [gate.wrap(target)]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    return lg, stream


def test_outside_writes_through():
    lg, stream = _sink()
    lg.info("a")
    assert stream.getvalue() == "a\n"


def test_inside_holds_then_releases_in_order():
    lg, stream = _sink()
    gate.install_record_factory()
    gate.enter()
    lg.info("one")
    lg.info("two")
    assert stream.getvalue() == ""  # nothing written inside
    res = gate.exit()
    assert stream.getvalue() == "one\ntwo\n"
    assert res["observability_overflow"] == 0
    assert res["measurement_log_level"] is not None


def test_exit_without_enter_returns_nulls():
    res = gate.exit()
    assert res == {
        "measurement_log_level": None,
        "measurement_log_config_hash": None,
        "observability_overflow": None,
    }


def test_no_file_open_inside(monkeypatch):
    lg, _ = _sink()
    gate.enter()
    calls = []
    monkeypatch.setattr(builtins, "open", lambda *a, **k: calls.append(a))
    lg.warning("held")
    gate.submit("event", {"stage_id": "measure"})
    monkeypatch.undo()
    gate.exit()
    assert calls == []


def test_srcfile_off_inside_and_restored():
    before = logging._srcfile
    gate.enter()
    assert logging._srcfile is None
    gate.exit()
    assert logging._srcfile == before


def test_event_seq_increasing_across_threads():
    gate.install_record_factory()
    lg, _ = _sink()
    gate.enter()

    def work():
        for _ in range(200):
            lg.info("x")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    held = [r.event_seq for _, r in gate._logs]
    assert len(held) == 1600
    assert len(set(held)) == 1600  # unique
    gate.exit()


def test_log_overflow_drops_oldest(monkeypatch):
    monkeypatch.setattr(gate, "LOG_CAPACITY", 3)
    lg, stream = _sink()
    gate.enter()
    for m in "abcde":
        lg.info(m)
    res = gate.exit()
    assert stream.getvalue() == "c\nd\ne\n"
    assert gate.counters()["gate_log_dropped"] == 2
    assert res["observability_overflow"] == 0  # logs are lossy, not invalid


def test_nonlossy_overflow_flags(monkeypatch):
    monkeypatch.setattr(gate, "NON_LOSSY_CAPACITY", 2)
    got = []
    gate.register_consumer("event", got.extend)
    gate.enter()
    assert gate.submit("event", {"n": 1})
    assert gate.submit("event", {"n": 2})
    assert not gate.submit("event", {"n": 3})
    res = gate.exit()
    assert res["observability_overflow"] == 1
    assert [r["n"] for r in got] == [1, 2]


def test_events_delivered_in_seq_order_after_exit():
    got = []
    gate.register_consumer("event", got.extend)
    gate.enter()
    gate.submit("event", {"n": 1})
    gate.submit("event", {"n": 2})
    assert got == []
    gate.exit()
    assert [r["n"] for r in got] == [1, 2]
    assert got[0]["event_seq"] < got[1]["event_seq"]


def test_forced_exit_on_reenter():
    lg, stream = _sink()
    gate.enter()
    lg.info("left")
    gate.enter()  # previous window never exited (task exception)
    assert stream.getvalue() == "left\n"
    assert gate.counters()["gate_forced_exit"] == 1
    gate.exit()


def test_consumer_failure_counted_not_raised():
    def bad(_records):
        raise RuntimeError("boom")

    gate.register_consumer("error", bad)
    gate.submit("error", {"e": 1})
    assert gate.counters()["gate_consumer_failed"] == 1


def test_trace_file_written_at_exit(tmp_path, monkeypatch):
    path = tmp_path / "trace.txt"
    monkeypatch.setenv(gate.TRACE_ENV, str(path))
    gate.enter()
    gate.exit()
    parts = path.read_text().split()
    assert len(parts) == 5 and float(parts[1]) <= float(parts[2])
