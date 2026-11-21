"""Sensor catalog readings: quality words, expected set, scopes, schema v2."""
import sqlite3
from pathlib import Path

from alems_sdk.thermal_catalog import (SCOPES, descriptor, expected_ids, sensor_quality,
                                       validate_descriptor)
from core.thermal.thermal_descriptors import hwmon_scope

ROOT = Path(__file__).resolve().parent.parent


def test_quality_words_cover_every_expected_sensor():
    vals, qual = sensor_quality({"a": 41.5, "b": None, "c": 300.0}, ["a", "b", "c", "d"])
    assert qual == {"a": "VALID", "b": "READ_FAILED", "c": "OUT_OF_RANGE", "d": "UNAVAILABLE"}
    assert vals["b"] is None and vals["d"] is None      # missing is None, never 0
    assert vals["c"] == 300.0                            # kept for audit


def test_expected_ids_follow_read_by():
    ds = [{"id": "x", "kind": "sensor", "read_by": "R"},
          {"id": "y", "kind": "sensor", "read_by": ["R", "S"]},
          {"id": "m", "kind": "derived", "read_by": "R"},
          {"id": "z", "kind": "sensor", "read_by": "discovery_utility"}]
    assert expected_ids(ds, "R") == ["x", "y"]          # derived values are not recorded
    assert expected_ids(ds, "R", kinds=("sensor", "derived")) == ["m", "x", "y"]


def test_hwmon_scopes():
    assert hwmon_scope("coretemp", "Package id 0") == "package"
    assert hwmon_scope("coretemp", "Core 3") == "core"
    assert hwmon_scope("nvme", "Composite") == "storage"
    assert hwmon_scope("spbm", "gpu") == "gpu"
    assert hwmon_scope("somechip", "temp1") == "unknown"


def test_descriptor_has_valid_scope():
    d = descriptor(id="iokit:PMU tdie1", kind="sensor", interface="iokit_helper",
                   reported_format="celsius_float", identity_key="PMU tdie1",
                   identity_confidence="strong", read_by="IOKitThermalReader", scope="die")
    assert d["scope"] in SCOPES and validate_descriptor(d) == []
    d["scope"] = "kitchen"
    assert validate_descriptor(d)


def test_migrations_build_the_timeline():
    c = sqlite3.connect(":memory:")
    c.executescript("""CREATE TABLE runs(run_id INTEGER PRIMARY KEY);
    CREATE TABLE thermal_samples(sample_id INTEGER PRIMARY KEY, run_id INT, timestamp_ns INT);
    CREATE TABLE thermal_zones(zone_id INTEGER PRIMARY KEY, zone_index INT, zone_type TEXT);
    CREATE TABLE te_session(session_id INTEGER PRIMARY KEY);
    CREATE TABLE te_thermal_samples(session_id INT, zone_id INT, timestamp_ns INT, temp_celsius REAL, quality_flag TEXT);
    CREATE TABLE te_gpu_temp_samples(session_id INT, gpu_index INT, timestamp_ns INT, temperature_c REAL, quality_flag TEXT);
    CREATE TABLE te_sensor(sensor_id INTEGER PRIMARY KEY AUTOINCREMENT, machine_profile TEXT NOT NULL, profile_sha256 TEXT NOT NULL, sensor_name TEXT NOT NULL, source_table TEXT NOT NULL, zone_type TEXT, zone_index INT, gpu_index INT, canonical_role TEXT NOT NULL, unit TEXT NOT NULL, nominal_resolution_c REAL NOT NULL, valid_min_c REAL NOT NULL, valid_max_c REAL NOT NULL, expected_update_interval_ms REAL, expected_update_interval_source TEXT, is_main INT NOT NULL DEFAULT 0, loaded_at_utc TEXT NOT NULL, interface_resolution_c REAL, observed_step_c REAL, resolution_status TEXT, catalog_sha256 TEXT);
    INSERT INTO te_session VALUES(1);""")
    c.executescript((ROOT / "migrations/schema/v124_thermal_samples_catalog_readings.sql").read_text())
    c.executescript((ROOT / "migrations/extensions/thermal_exposure/e005_sensor_samples_and_catalog.sql").read_text())
    c.execute("""INSERT INTO thermal_samples(run_id,timestamp_ns,sensors_json,sensor_quality_json,catalog_sha256)
                 VALUES(5,100,'{"s1": 41.5, "s2": null}','{"s1":"VALID","s2":"READ_FAILED"}','h')""")
    c.execute("INSERT INTO te_sensor_samples(session_id,catalog_id,timestamp_ns,temp_celsius,quality_flag) "
              "VALUES(1,'s1',200,40.9,'VALID')")
    rows = c.execute("SELECT source_phase, catalog_id, temp_celsius, quality_flag "
                     "FROM te_v_sensor_timeline ORDER BY timestamp_ns, catalog_id").fetchall()
    assert rows == [("run", "s1", 41.5, "VALID"), ("run", "s2", None, "READ_FAILED"),
                    ("between", "s1", 40.9, "VALID")]
    try:
        c.execute("INSERT INTO te_sensor_samples(session_id,catalog_id,timestamp_ns,temp_celsius,quality_flag) "
                  "VALUES(1,'s1',300,0,'UNAVAILABLE')")
        raise AssertionError("UNAVAILABLE with a value must be rejected")
    except sqlite3.IntegrityError:
        pass


def test_near_zero_is_not_a_reading():
    vals, qual = sensor_quality({"sen4": 0.05, "ok": 48.0}, ["sen4", "ok"])
    assert qual == {"sen4": "OUT_OF_RANGE", "ok": "VALID"}
    assert vals["sen4"] == 0.05                          # kept for audit


class _Reader:
    def __init__(self, values):
        self.values, self.inits = values, 0

    def initialize(self):
        self.inits += 1

    def read_all_thermal(self):
        return dict(self.values)


def _engine(zone, platform):
    from core.energy_engine import EnergyEngine
    e = EnergyEngine.__new__(EnergyEngine)              # no hardware set-up
    e.sensor, e._platform_thermal, e._platform_ready = zone, platform, False
    return e


def test_cpu_temp_from_platform_reader_when_zone_reader_has_none():
    platform = _Reader({"cpu_package": 45.75})
    e = _engine(_Reader({}), platform)
    out = e._fill_cpu_package({"iwlwifi_1": 31.0, "cpu_temp": None})
    assert out["cpu_temp"] == 45.75 and out["package_celsius"] == 45.75 and out["iwlwifi_1"] == 31.0
    e._fill_cpu_package({"cpu_temp": None})
    assert platform.inits == 1                           # initialised once


def test_cpu_temp_never_invented_or_overwritten():
    e = _engine(_Reader({}), _Reader({"cpu_package": None}))
    assert e._fill_cpu_package({"cpu_temp": None}) == {"cpu_temp": None}
    e = _engine(_Reader({}), _Reader({"cpu_package": 50.0}))
    assert e._fill_cpu_package({"cpu_temp": 61.0}) == {"cpu_temp": 61.0}
    same = _Reader({"cpu_package": 50.0})
    assert _engine(same, same)._fill_cpu_package({"cpu_temp": None}) == {"cpu_temp": None}
