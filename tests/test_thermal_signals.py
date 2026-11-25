"""Thermal-response signals: SDK helpers, NVML reader, migrations v130, v131, e006."""
import json
import sqlite3
import sys
import types
from pathlib import Path

from alems_sdk.thermal_catalog import (counter_deltas, signal_descriptor, signals_sha256,
                                       typed_value, validate_signal)

ROOT = Path(__file__).resolve().parent.parent


def test_unverified_signal_never_enters_analysis():
    d = signal_descriptor("spbm:prochot", "level", "spbm", "prochot", "raw", "invalidated", True,
                          "ARMThermalReader", scope="package")
    assert d["analysis_enabled"] is False and validate_signal(d) == []
    d["analysis_enabled"] = True
    assert validate_signal(d)


def test_typed_value_keeps_large_integers_exact():
    assert typed_value(2 ** 64 - 1) == (None, None, "18446744073709551615")
    assert typed_value(7) == (7, None, None) and typed_value(None) == (None, None, None)


def test_counter_reset_is_a_discontinuity_not_a_negative_delta():
    r = counter_deltas([(0, 100), (1, 130), (2, None), (3, 175), (4, 5), (5, 20)])
    assert r["total"] == 90 and r["discontinuities"] == 1 and r["complete"] is False
    assert all(d >= 0 for _, _, d in r["increments"])


def test_catalog_hash_ignores_notes_only():
    d = signal_descriptor("a", "level", "s", "f", "C", "empirical", True, "R")
    assert signals_sha256([d]) == signals_sha256([dict(d, notes="x")])
    assert signals_sha256([d]) != signals_sha256([dict(d, raw_unit="K")])


class _Field:
    def __init__(self, ret, val):
        self.nvmlReturn = ret
        self.value = types.SimpleNamespace(ullVal=val)


def _fake_pynvml(monkeypatch):
    p = types.ModuleType("pynvml")
    p.nvmlInit = lambda: None
    p.nvmlDeviceGetCount = lambda: 1
    p.nvmlDeviceGetHandleByIndex = lambda i: "h%d" % i
    p.nvmlDeviceGetUUID = lambda h: b"GPU-1234"
    p.nvmlDeviceGetName = lambda h: "NVIDIA GB10"
    p.nvmlSystemGetDriverVersion = lambda: "580.159.03"
    p.nvmlSystemGetNVMLVersion = lambda: "13.580"
    p.nvmlClocksEventReasonGpuIdle = 1
    p.nvmlClocksEventReasonSwPowerCap = 4
    p.nvmlClocksEventReasonAll = 255
    p.nvmlDeviceGetCurrentClocksEventReasons = lambda h: 5
    p.NVML_FI_DEV_CLOCKS_EVENT_REASON_SW_POWER_CAP = 230
    p.NVML_FI_DEV_CLOCKS_EVENT_REASON_HW_THERM_SLOWDOWN = 234
    p.nvmlDeviceGetFieldValues = lambda h, ids: [_Field(0, 8627177000), _Field(3, 0)]
    monkeypatch.setitem(sys.modules, "pynvml", p)


def test_nvml_signals_raw_values_and_failed_fields(monkeypatch):
    _fake_pynvml(monkeypatch)
    from core.readers.nvml_signals import NvmlSignals
    n = NvmlSignals()
    descs = {d["id"]: d for d in n.describe_signals()}
    assert all(validate_signal(d) == [] for d in descs.values())
    assert descs["nvml:gpu0:clocks_event_reasons"]["decoder"] == {"1": "GpuIdle", "4": "SwPowerCap"}
    v = n.read_signals()
    assert v["nvml:gpu0:clocks_event_reasons"]["value"] == 5             # two reasons at once, raw
    assert v["nvml:gpu0:reason_ns:sw_power_cap"]["value"] == 8627177000
    assert v["nvml:gpu0:reason_ns:hw_thermal_slowdown"] == {"value": None, "unit": "ns",
                                                            "status": "READ_FAILED"}
    assert v["nvml:driver_version"]["value"] == "580.159.03"
    assert "nvml:gpu0:temp_margin_c" not in v                           # not offered: not described


def test_no_nvidia_driver_means_no_signals(monkeypatch):
    p = types.ModuleType("pynvml")

    def _fail():
        raise RuntimeError("driver not loaded")
    p.nvmlInit = _fail
    monkeypatch.setitem(sys.modules, "pynvml", p)
    from core.readers.nvml_signals import NvmlSignals
    n = NvmlSignals()
    assert n.ok is False and n.describe_signals() == [] and n.read_signals() == {}


def test_migrations_signals_and_cooling():
    c = sqlite3.connect(":memory:")
    c.executescript("""CREATE TABLE runs(run_id INTEGER PRIMARY KEY); CREATE TABLE cooling_devices(device_id INTEGER PRIMARY KEY);
    CREATE TABLE thermal_samples(sample_id INTEGER PRIMARY KEY, run_id INT, timestamp_ns INT);
    CREATE TABLE te_session(session_id INTEGER PRIMARY KEY); INSERT INTO te_session VALUES(1);
    INSERT INTO runs VALUES(1); INSERT INTO cooling_devices VALUES(1);
    CREATE TABLE cooling_samples (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, device_id INTEGER NOT NULL, timestamp_ns INTEGER NOT NULL, cur_state INTEGER NOT NULL, quality_flag TEXT NOT NULL DEFAULT 'VALID', invalid_reason TEXT, global_run_id TEXT, FOREIGN KEY(run_id) REFERENCES runs(run_id), FOREIGN KEY(device_id) REFERENCES cooling_devices(device_id));
    INSERT INTO cooling_samples(run_id,device_id,timestamp_ns,cur_state,quality_flag) VALUES(1,1,10,0,'VALID'),(1,1,11,0,'MISSING');""")
    for f in ("migrations/schema/v130_thermal_samples_signals.sql",
              "migrations/schema/v131_cooling_state_nullable.sql",
              "migrations/extensions/thermal_exposure/e006_signal_samples.sql"):
        c.executescript((ROOT / f).read_text())
    assert c.execute("SELECT cur_state FROM cooling_samples ORDER BY id").fetchall() == [(0,), (0,)]  # unchanged
    assert c.execute("SELECT cur_state FROM v_cooling_samples_valid ORDER BY id").fetchall() == [(0,), (None,)]
    c.execute("INSERT INTO cooling_samples(run_id,device_id,timestamp_ns,cur_state,quality_flag) VALUES(1,1,12,NULL,'MISSING')")
    c.execute("INSERT INTO thermal_samples(run_id,timestamp_ns,signals_json) VALUES(1,100,?)",
              (json.dumps({"spbm:pl_level": {"value": 1, "status": "VALID"}}),))
    c.execute("INSERT INTO te_signal_samples(session_id,signal_id,timestamp_ns,value_text,status) "
              "VALUES(1,'nvml:driver_version',200,'580.159.03','VALID')")
    rows = c.execute("SELECT source_phase, signal_id, value FROM te_v_signal_timeline ORDER BY timestamp_ns").fetchall()
    assert rows == [("run", "spbm:pl_level", 1), ("between", "nvml:driver_version", "580.159.03")]
    for bad in ("INSERT INTO te_signal_samples(session_id,signal_id,timestamp_ns,status) VALUES(1,'x',1,'VALID')",
                "INSERT INTO te_signal_samples(session_id,signal_id,timestamp_ns,value_int,value_text,status) "
                "VALUES(1,'x',1,1,'a','VALID')"):
        try:
            c.execute(bad)
            raise AssertionError("must be rejected: " + bad)
        except sqlite3.IntegrityError:
            pass
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
