"""
Tests for core/safety/thermal_guard.py with fake sources, a fake clock and a
fake server that ignores cancellation. No hardware, no store.
"""
import json

import pytest

from core.safety.thermal_guard import (GuardConfigError, GuardRefused, NvmlMarginSource, ThermalGuard,
                                       ZoneTripSource, load_config, reading)

CFG = {
    "policy_version": "1", "enabled": True, "interval_s": 1.0,
    "start_floor_headroom_c": 28.0, "start_idle_margin_c": None, "start_stable_s": 30.0,
    "max_wait_s": 600.0, "stop_headroom_c": 15.0, "min_rest_s": 30.0, "stale_after_s": 3.0,
    "allow_without_source": False, "rise_warn_c_per_s": None,
}


class Clock(object):
    def __init__(self):
        self.mono = 1_000_000_000
        self.on_sleep = None

    def mono_ns(self):
        return self.mono

    def utc_ns(self):
        return 1_800_000_000_000_000_000 + self.mono

    def sleep(self, s):
        self.mono += int(s * 1e9)
        if self.on_sleep:
            self.on_sleep()


class Source(object):
    name = "fake"

    def __init__(self, **values):
        self.values = dict(values)

    def read(self, now):
        return [reading("fake", comp, "slowdown_margin", v, "fresh" if v is not None else "failed", now)
                for comp, v in self.values.items()]


class Log(object):
    def __init__(self, order, fail=False):
        self.lines, self.order, self.fail = [], order, fail

    def __call__(self, rec):
        if self.fail:
            return False
        self.lines.append(rec)
        self.order.append(("log", rec["event_id"]))
        return True


class Store(object):
    def __init__(self, order, fail=False):
        self.episodes, self.events, self.samples, self.order, self.fail = {}, [], [], order, fail

    def store_episode(self, ep):
        self.episodes[ep["episode_id"]] = ep

    def update_episode(self, ep):
        self.episodes[ep["episode_id"]] = ep

    def store_event(self, ev):
        if self.fail:
            raise IOError("disk full")
        self.events.append(ev)
        self.order.append(("store", ev["event_id"]))

    def store_sample(self, s):
        self.samples.append(s)


def make(cfg=None, source=None, log_fail=False, store_fail=False):
    clock, order = Clock(), []
    src = source or Source(gpu0=33.0)
    log, store = Log(order, log_fail), Store(order, store_fail)
    g = ThermalGuard(dict(CFG, **(cfg or {})), [src], log, store, entry_path="test",
                     mono_ns=clock.mono_ns, utc_ns=clock.utc_ns, sleep=clock.sleep)
    clock.on_sleep = g.tick
    return g, clock, src, log, store, order


def types(store):
    return [e["event_type"] for e in store.events]


# ---------------------------------------------------------------- config
def test_missing_key_refuses_and_names_it():
    bad = dict(CFG)
    del bad["min_rest_s"]
    with pytest.raises(GuardConfigError, match="min_rest_s"):
        load_config(bad)


def test_bool_is_not_a_number_and_stop_below_start():
    with pytest.raises(GuardConfigError):
        load_config(dict(CFG, interval_s=True))
    with pytest.raises(GuardConfigError):
        load_config(dict(CFG, stop_headroom_c=30.0))


# ---------------------------------------------------------------- sources
def test_no_source_refuses():
    g, _, _, _, store, _ = make(source=Source())
    with pytest.raises(GuardRefused) as e:
        g.open()
    assert e.value.code == "no_source"
    assert "no_source" in types(store)
    assert store.episodes[g.episode["episode_id"]]["end_reason"] == "no_source"


def test_nvml_margin_parse():
    class Sig(object):
        ok = True

        def read_signals(self):
            return {"nvml:gpu0:temp_margin_c": {"value": 33, "unit": "C", "status": "VALID"},
                    "nvml:gpu0:clocks_event_reasons": {"value": 4, "unit": "bitmask", "status": "VALID"},
                    "nvml:gpu0:reason_ns:sw_power_cap": {"value": 10, "unit": "ns", "status": "VALID"},
                    "nvml:gpu1:temp_margin_c": {"value": None, "unit": "C", "status": "READ_FAILED"}}
    r = {x["component"]: x for x in NvmlMarginSource(Sig()).read(5)}
    assert r["gpu0"]["headroom_c"] == 33.0 and r["gpu0"]["quality"] == "fresh"
    assert r["gpu0"]["raw"] == {"clocks_event_reasons": 4, "reason_ns": {"sw_power_cap": 10}}
    assert r["gpu1"]["headroom_c"] is None and r["gpu1"]["quality"] == "failed"


# ---------------------------------------------------------------- gate
def test_gate_opens_after_stable_period():
    g, clock, _, _, store, _ = make()
    g.open()
    t0 = clock.mono
    out = g.admit()
    assert out["start_level_c"] == 28.0
    assert clock.mono - t0 >= 30e9
    assert "gate_open" in types(store)


def test_gate_restarts_on_a_dip():
    g, clock, src, _, _, _ = make()
    g.open()
    seen = {"n": 0}

    def dip():
        seen["n"] += 1
        src.values["gpu0"] = 25.0 if seen["n"] == 20 else 33.0
        g.tick()
    clock.on_sleep = dip
    t0 = clock.mono
    g.admit()
    assert clock.mono - t0 >= 50e9          # 20 s, dip, then a fresh 30 s


def test_gate_timeout_warns_and_keeps_waiting_until_cool():
    g, clock, src, _, store, _ = make(source=Source(gpu0=20.0))
    g.open()
    seen = {"n": 0}

    def cool_later():
        seen["n"] += 1
        src.values["gpu0"] = 20.0 if seen["n"] < 1300 else 33.0   # hot for about 21 minutes
        g.tick()
    clock.on_sleep = cool_later
    out = g.admit()                                     # never raises: it waits
    assert out["headroom_c"] == 33.0
    t = types(store)
    assert t.count("idle_headroom_insufficient") == 2    # one warning per 600 s period
    assert "gate_timeout" not in t and t[-1] == "gate_open"


def test_later_gate_warning_is_gate_timeout():
    g, clock, src, _, store, _ = make(cfg={"max_wait_s": 60.0})
    g.open()
    g.admit()
    g.workload_started()
    g.workload_ended()
    seen = {"n": 0}

    def cool_later():
        seen["n"] += 1
        src.values["gpu0"] = 20.0 if seen["n"] < 120 else 33.0
        g.tick()
    clock.on_sleep = cool_later
    src.values["gpu0"] = 20.0
    g.admit()
    assert "gate_timeout" in types(store)


def test_rest_is_never_shortened_by_backoff():
    g, clock, _, _, _, _ = make()
    g.open()
    g.admit()
    g.workload_started()
    g.workload_ended()
    end = clock.mono
    g.admit(requested_rest_s=1.0)
    assert clock.mono - end >= 30e9


def test_idle_reference_rule():
    g, _, _, _, _, _ = make(cfg={"start_idle_margin_c": 5.0})
    g.open()
    with pytest.raises(GuardRefused) as e:
        g.start_level()
    assert e.value.code == "idle_reference_invalid"
    g.set_idle_reference(33.0, {"server": "vllm"})
    assert g.start_level() == 28.0
    g.set_idle_reference(35.0, {"server": "vllm"})
    assert g.start_level() == 30.0
    g.invalidate_idle_reference("server restart")
    with pytest.raises(GuardRefused):
        g.start_level()


# ---------------------------------------------------------------- crossing, never abort
def test_stop_level_crossing_is_recorded_once_and_never_interrupts():
    g, clock, src, _, store, _ = make()
    g.open()
    g.admit()
    g.workload_started()
    src.values["gpu0"] = 14.0
    g.tick()
    g.tick()
    crossings = [e for e in store.events if e["event_type"] == "warn"
                 and "stop_level_crossed" in (e["detail_json"] or "")]
    assert len(crossings) == 1 and crossings[0]["value_c"] == 14.0
    g.workload_started()                                # nothing is latched: work may still be tracked
    g.workload_ended()
    g.workload_ended()
    src.values["gpu0"] = 33.0
    t0 = clock.mono
    g.admit()                                           # next run waits for rest and gate, then starts
    assert clock.mono - t0 >= 30e9
    assert store.episodes[g.episode["episode_id"]]["status"] == "running"


def test_vanished_hottest_source_never_improves_minimum_and_blocks_admission():
    g, clock, src, _, store, _ = make(source=Source(gpu0=40.0, gpu1=20.0))
    g.open()
    src.values["gpu1"] = None
    st = g.tick()
    assert st["min"] == 20.0 and st["quality"] == "stale"
    for _ in range(4):
        g._sleep(1.0)                                    # each sleep is one watchdog tick
    assert g._state["blind"] and "source_stale" in types(store)
    seen = {"n": 0}

    def back_later():
        seen["n"] += 1
        if seen["n"] == 50:
            src.values["gpu1"] = 30.0
        g.tick()
    clock.on_sleep = back_later
    t0 = clock.mono
    g.admit()                                           # waits while blind, opens 30 s after the source returns
    assert clock.mono - t0 >= 80e9


def test_zone_source_uses_only_trustworthy_critical_trips():
    zones = [{"index": 0, "type": "soc", "dir": "z0"}, {"index": 1, "type": "board", "dir": "z1"},
             {"index": 2, "type": "fixed", "dir": "z2"}, {"index": 3, "type": "dead", "dir": "z3"}]
    trips = {"z0": [{"type": "passive", "temp_c": 90.0}, {"type": "critical", "temp_c": 105.0}],
             "z1": [{"type": "passive", "temp_c": 80.0}],
             "z2": [{"type": "critical", "temp_c": 20.0}],
             "z3": [{"type": "critical", "temp_c": 100.0}]}
    temps = {"z0": 60.0, "z1": 50.0, "z2": 20.0, "z3": None}
    src = ZoneTripSource(lambda: zones, lambda d: trips[d], lambda d: temps[d])
    r = {x["component"]: x for x in src.read(7)}
    assert r["0:soc"]["headroom_c"] == 45.0 and r["0:soc"]["quality"] == "fresh"
    assert r["0:soc"]["limit_type"] == "critical_trip" and r["0:soc"]["raw"]["critical_c"] == 105.0
    assert r["1:board"]["quality"] == "invalid" and r["1:board"]["headroom_c"] is None   # no critical trip
    assert r["2:fixed"]["quality"] == "invalid"                                         # trip not above temp
    assert r["3:dead"]["quality"] == "failed"                                           # unreadable temperature


def test_invalid_zone_never_decides_the_minimum():
    zones = [{"index": 2, "type": "fixed", "dir": "z2"}]
    src_zone = ZoneTripSource(lambda: zones, lambda d: [{"type": "critical", "temp_c": 20.0}], lambda d: 20.0)
    g, _, _, _, _, _ = make(source=Source(gpu0=33.0))
    g._sources.append(src_zone)
    g.open()
    st = g.tick()
    assert st["min"] == 33.0 and st["reading"]["source"] == "fake"


def test_missing_values_stay_null_in_samples():
    g, _, src, _, store, _ = make()
    g.open()
    src.values["gpu0"] = None
    for _ in range(5):
        g._sleep(1.0)
    last = store.samples[-1]
    assert last["min_headroom_c"] is None and last["gpu_temp_c"] is None
    assert last["min_quality"] == "unavailable"
    assert json.loads(last["readings_json"])[0]["headroom_c"] is None


# ---------------------------------------------------------------- records
def test_log_before_store_and_log_failure_flagged():
    g, _, _, log, store, order = make()
    g.open()
    for ev in store.events:
        assert order.index(("log", ev["event_id"])) < order.index(("store", ev["event_id"]))
        assert ev["log_written"] == 1
    g2, _, _, _, store2, _ = make(log_fail=True)
    g2.open()
    assert all(e["log_written"] == 0 for e in store2.events)


def test_store_failure_never_loses_the_host_log_line():
    g, _, src, log, _, _ = make(store_fail=True)
    g.open()
    g.workload_started()
    src.values["gpu0"] = 10.0
    g.tick()
    assert any(l["event_type"] == "warn" for l in log.lines)
    assert any(e["event_type"] == "warn" for e in g.unstored_events)
