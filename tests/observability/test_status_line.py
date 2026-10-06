"""
Tests for the in place status line (master amendment 5.2a heartbeat).

Covers: disabled off a terminal and with ALEMS_HEARTBEAT_S=0; draws carriage
return lines on a terminal; erased at stop; gate reports the interval; the
countdown sleeps the full time.
"""

import io
import time

from core.observability import gate, status


class _Tty(io.StringIO):
    """StringIO that claims to be a terminal."""

    def isatty(self):
        return True


def _use(stream, monkeypatch):
    monkeypatch.setattr(status, "_stream", stream)


def test_disabled_without_terminal(monkeypatch):
    _use(io.StringIO(), monkeypatch)
    assert status.window_start() == 0.0
    status.window_stop()


def test_disabled_by_zero_interval(monkeypatch):
    _use(_Tty(), monkeypatch)
    monkeypatch.setenv(status.ENV, "0")
    assert status.window_start() == 0.0


def test_draws_and_erases(monkeypatch):
    buf = _Tty()
    _use(buf, monkeypatch)
    monkeypatch.setenv(status.ENV, "0.05")
    status.set_context(phase="agentic", rep=2, total=4)
    assert status.window_start() == 0.05
    time.sleep(0.2)
    status.window_stop()
    out = buf.getvalue()
    assert "\rmeasuring" not in out  # spinner precedes the word
    assert "measuring agentic" in out and "repetition 2/4" in out and "25%" in out
    assert out.endswith("\r\033[2K\r")


def test_gate_reports_interval(monkeypatch):
    _use(_Tty(), monkeypatch)
    monkeypatch.setenv(status.ENV, "0.05")
    gate.reset_for_tests()
    gate.enter()
    result = gate.exit()
    assert result["measurement_heartbeat_s"] == 0.05


def test_wait_sleeps_full_time(monkeypatch):
    _use(_Tty(), monkeypatch)
    monkeypatch.setenv(status.ENV, "0.05")
    t0 = time.perf_counter()
    status.wait(0.2, "cool down")
    assert time.perf_counter() - t0 >= 0.19
