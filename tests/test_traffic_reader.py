"""Tests for the traffic reader family (45.3a): collector and pravaha plugin, no GPU needed."""
import sys
import types

import pytest


class _Obs:
    def __init__(self, interval_ms=10, device=0):
        self.args = (interval_ms, device)

    def start(self):
        return self

    def stop(self):
        pass

    def to_dict(self):
        return {"samples": [{"t_start_ns": 100, "t_end_ns": 110, "copy_bytes": 5,
                             "kernel_read_miss_bytes": None, "kernel_write_bytes": 0,
                             "all_read_miss_bytes": 7, "all_write_bytes": 3}]}

    def baseline(self):
        return {"copy_bytes_per_s": 1.0}

    def quality(self):
        return {"gaps": 0}


@pytest.fixture
def fake_pravaha(monkeypatch):
    mod = types.ModuleType("pravaha_mem")
    mod.status = lambda: {"ready": True, "fix": [],
                          "provenance": {"pravaha": "1.0.0", "driver": "d", "cuda": "c"}}
    mod.observe = _Obs
    monkeypatch.setitem(sys.modules, "pravaha_mem", mod)
    return mod


def _reader():
    from alems_plugin_pravaha.reader import PravahaTrafficReader
    return PravahaTrafficReader()


def test_measured_rows_keep_nulls(fake_pravaha, monkeypatch):
    monkeypatch.setenv("ALEMS_PRAVAHA_INTERVAL_MS", "20")
    r = _reader()
    assert r.is_available()
    r.start()
    res = r.stop()
    assert res["status"] == "measured" and res["sample_count"] == 1 and res["interval_ms"] == 20
    row = res["samples"][0]
    assert row["kernel_read_miss_bytes"] is None and row["kernel_write_bytes"] == 0
    assert row["interval_ns"] == 10 and row["measurement_scope"] == "device_wide"
    assert res["pravaha_version"] == "1.0.0" and '"gaps": 0' in res["quality_json"]


def test_not_ready_gives_reason(fake_pravaha):
    fake_pravaha.status = lambda: {"ready": False, "fix": ["run pravaha install"], "provenance": {}}
    r = _reader()
    assert not r.is_available()
    res = r.unavailable_result()
    assert res["status"] == "unavailable" and "pravaha install" in res["reason"]


def test_disabled_by_config(fake_pravaha, monkeypatch):
    monkeypatch.setenv("ALEMS_PRAVAHA_ENABLED", "0")
    r = _reader()
    assert not r.is_available() and "ALEMS_PRAVAHA_ENABLED" in r.unavailable_result()["reason"]


def test_collector_records_start_failure(fake_pravaha, monkeypatch):
    from core.readers import traffic_collector as tc

    class _Boom(_Obs):
        def start(self):
            raise RuntimeError("HARDWARE_BUSY")

    fake_pravaha.observe = _Boom
    from alems_plugin_pravaha.reader import PravahaTrafficReader
    monkeypatch.setattr(tc.TrafficCollector, "_registered", staticmethod(lambda: {"pravaha": PravahaTrafficReader}))
    c = tc.TrafficCollector()
    c.start()
    res = c.stop()
    assert len(res) == 1 and res[0]["status"] == "failed" and "HARDWARE_BUSY" in res[0]["reason"]


def test_collector_no_readers(monkeypatch):
    from core.readers import traffic_collector as tc
    monkeypatch.setattr(tc.TrafficCollector, "_registered", staticmethod(lambda: {}))
    c = tc.TrafficCollector()
    c.start()
    assert c.stop() == []
