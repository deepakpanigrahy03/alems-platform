"""Tests for alems_sdk.tool_latency (config driven tool result hold)."""
import pytest

from alems_sdk import tool_latency as tl


def teardown_function(_):
    tl.configure(None)


def test_absent_or_disabled_is_inactive():
    assert tl.configure(None) is None and tl.hold() == {}
    assert tl.configure({"enabled": False, "delay_s": 1}) is None and tl.hold() == {}


@pytest.mark.parametrize("bad", [
    {"enabled": True, "wait_mode": "sleep", "apply_to": "all"},                      # no delay
    {"enabled": True, "delay_s": -1, "wait_mode": "sleep", "apply_to": "all"},       # negative
    {"enabled": True, "delay_s": 1, "wait_mode": "nap", "apply_to": "all"},          # bad mode
    {"enabled": True, "delay_s": 1, "wait_mode": "poll", "apply_to": "all"},         # poll without poll_ms
    {"enabled": True, "delay_s": 1, "wait_mode": "sleep", "apply_to": "some"},       # bad apply_to
])
def test_invalid_sections_refused(bad):
    with pytest.raises(ValueError):
        tl.configure(bad)


@pytest.mark.parametrize("mode,extra", [("sleep", {}), ("poll", {"poll_ms": 5}), ("poll", {"poll_ms": 0})])
def test_hold_waits_and_reports(mode, extra):
    cfg = dict({"enabled": True, "delay_s": 0.05, "wait_mode": mode, "apply_to": "all"}, **extra)
    tl.configure(cfg)
    out = tl.hold()
    assert out["tool_wait_mode"] == mode
    assert 45.0 <= out["injected_delay_ms"] < 500.0


def test_apply_to_success_skips_failures():
    tl.configure({"enabled": True, "delay_s": 0.05, "wait_mode": "sleep", "apply_to": "success"})
    assert tl.hold(success=False) == {}
    assert tl.hold(success=True)["injected_delay_ms"] >= 45.0


def test_reconfigure_clears():
    tl.configure({"enabled": True, "delay_s": 0.01, "wait_mode": "sleep", "apply_to": "all"})
    tl.configure(None)
    assert tl.active() is None and tl.hold() == {}
