"""Sensor catalog contract, probe, sysfs descriptors and discovery merge (no hardware)."""

import itertools
import json

import pytest

from alems_sdk import thermal_catalog as tc
from alems_sdk import thermal_probe as tp
from alems_sdk.readers import ThermalReaderABC


# ── SDK contract ────────────────────────────────────────────────────────────

class _OldReader(ThermalReaderABC):
    """A reader written before the catalog existed (implements nothing new)."""

    def is_available(self):
        return True

    def get_name(self):
        return "OldReader"

    def read_all_thermal(self):
        return {"cpu_temp": 51.0, "zone_a": None}

    def read_temperatures(self):
        return {}


def test_old_reader_keeps_working_and_reports_derived_not_sensors():
    r = _OldReader()
    descs = r.describe_sensors()
    assert {d["kind"] for d in descs} == {"derived"}
    assert all(not tc.validate_descriptor(d) for d in descs)
    assert r.read_sensors() == {"OldReader:cpu_temp": 51.0, "OldReader:zone_a": None}
    assert r.read_signals() == {} and r.describe_signals() == []


def test_descriptor_validation_rejects_zero_for_missing():
    d = tc.descriptor("x", "sensor", "sysfs_thermal_zone", "millidegree_c", "k", "strong", "R",
                      interface_resolution_c=0.001, limits=[tc.limit("t", "critical", None, "sysfs")])
    assert tc.validate_descriptor(d) == []
    assert tc.validate_descriptor(dict(d, interface_resolution_c=0))
    assert tc.validate_descriptor(dict(d, limits=[tc.limit("t", "critical", 0, "sysfs")]))
    assert tc.validate_descriptor(dict(d, kind="derived"))          # derived needs a formula


def test_catalog_hash_is_deterministic_and_ignores_notes_and_order():
    a = tc.descriptor("a", "sensor", "i", "f", "k", "strong", "R")
    b = tc.descriptor("b", "sensor", "i", "f", "k", "strong", "R")
    assert tc.catalog_sha256([a, b]) == tc.catalog_sha256([b, dict(a, notes="x")])
    assert tc.catalog_sha256([a]) != tc.catalog_sha256([dict(a, interface_resolution_c=0.1)])


# ── probe ───────────────────────────────────────────────────────────────────

def _clock():
    t = [0.0]
    return t, (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + max(s, 0.001)))


def test_probe_steps_duplicates_constant_and_failures():
    t, clock, sleep = _clock()
    seq = itertools.cycle([60.5, 60.5, 60.6, 60.6, 60.5, 60.7])

    def zones():
        t[0] += 0.0005
        v = next(seq)
        return {"A": v, "B": v, "C": 57.0}

    def broken():
        raise OSError("gone")
    r = tp.probe({"zones": zones, "broken": broken}, 2.0, hz=50, clock=clock, sleep=sleep)
    s = r["sensors"]
    assert (s["A"]["observed_value_step_c"], s["A"]["resolution_status"]) == (0.1, "measured")
    assert s["A"]["possible_duplicate_of"] == ["B"]                    # advisory only
    assert (s["C"]["observed_value_step_c"], s["C"]["resolution_status"]) == \
        (None, "insufficient_transitions")
    assert r["sources"]["broken"]["errors"] == {"OSError": r["probe"]["rounds"]}
    assert r["probe"]["achieved_s"] >= 2.0


def test_single_step_is_not_enough_evidence():
    t, clock, sleep = _clock()
    vals = iter([50.0] * 10 + [51.0] * 200)
    r = tp.probe({"z": lambda: {"z": next(vals, 51.0)}}, 1.0, hz=50, clock=clock, sleep=sleep)
    assert r["sensors"]["z"]["resolution_status"] == "insufficient_transitions"


# ── sysfs descriptors ───────────────────────────────────────────────────────

def _zone(root, n, ztype, temp, trips=()):
    d = root / ("thermal_zone%d" % n)
    d.mkdir()
    (d / "type").write_text(ztype + "\n")
    (d / "temp").write_text("%d\n" % temp)
    for i, (kind, val) in enumerate(trips):
        (d / ("trip_point_%d_type" % i)).write_text(kind + "\n")
        (d / ("trip_point_%d_temp" % i)).write_text(val + "\n")
    return d


def test_zone_descriptors_trip_points_and_identity(tmp_path, monkeypatch):
    from core.thermal import thermal_descriptors as td
    monkeypatch.setattr(td, "SYSFS_THERMAL", str(tmp_path))
    _zone(tmp_path, 0, "acpitz", 60600, [("passive", "95000"), ("critical", "garbage")])
    _zone(tmp_path, 1, "acpitz", 60500)
    _zone(tmp_path, 2, "x86_pkg_temp", 45000)
    descs = {d["id"]: d for d in td.zone_descriptors("ThermalReaderV2")}
    z0 = descs["thermal_zone:0:acpitz"]
    assert [(l["type"], l["temp_c"]) for l in z0["limits"]] == [("passive", 95.0), ("critical", None)]
    assert z0["identity_confidence"] == "ambiguous"                     # two acpitz, no device
    assert descs["thermal_zone:2:x86_pkg_temp"]["identity_confidence"] == "inferred"
    assert z0["interface_resolution_c"] == 0.001 and z0["reported_format"] == "millidegree_c"
    assert td.read_zone_c(str(tmp_path / "thermal_zone0")) == 60.6


def test_hwmon_inputs_with_labels_and_limits(tmp_path, monkeypatch):
    from core.thermal import thermal_descriptors as td
    monkeypatch.setattr(td, "SYSFS_HWMON", str(tmp_path))
    h = tmp_path / "hwmon3"
    h.mkdir()
    (h / "name").write_text("coretemp\n")
    (h / "temp1_input").write_text("48000\n")
    (h / "temp1_label").write_text("Package id 0\n")
    (h / "temp1_crit").write_text("100000\n")
    (h / "temp2_input").write_text("47000\n")
    descs = td.hwmon_descriptors()
    assert [d["label"] for d in descs] == ["Package id 0", "temp2"]
    assert descs[0]["read_by"] == "discovery_utility"
    assert descs[0]["limits"][0]["temp_c"] == 100.0


# ── discovery merge and write ───────────────────────────────────────────────

def _dt():
    import importlib.util
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "detect_thermal.py"
    spec = importlib.util.spec_from_file_location("detect_thermal", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_reads_each_sensor_once_and_unions_readers():
    dt = _dt()
    d = tc.descriptor("thermal_zone:0:acpitz", "sensor", "sysfs_thermal_zone", "millidegree_c",
                      "k", "ambiguous", "ThermalReaderV2")
    calls = {"v2": 0, "arm": 0}

    def v2():
        calls["v2"] += 1
        return {"thermal_zone:0:acpitz": 60.0}

    def arm():
        calls["arm"] += 1
        return {"thermal_zone:0:acpitz": 60.0, "ARM:cpu_temp": 60.0}
    derived = tc.descriptor("ARM:cpu_temp", "derived", "derived", "celsius_float", "k", "strong",
                            "ARM", formula="mean")
    catalog, rounds, invalid = dt.merge_catalog([("V2", [d], v2), ("ARM", [dict(d, read_by="ARM"), derived], arm)])
    assert invalid == []
    assert [c["read_by"] for c in catalog if c["id"] == d["id"]] == [["ThermalReaderV2", "ARM"]]
    assert rounds["ARM"]() == {"ARM:cpu_temp": 60.0}                   # zone read through V2 only


def test_write_keeps_other_keys_and_no_sensors_is_not_an_error(tmp_path, monkeypatch):
    dt = _dt()
    cfg = tmp_path / "hw_config.json"
    cfg.write_text(json.dumps({"thermal": {"paths": {}}, "cpu_model": "x"}))
    monkeypatch.setattr(dt, "collect_sources", lambda c: ([], ["zones: none"]))
    section = dt.build({}, 0.1, 50)
    assert section["status"] == "no_sensors" and "no supported thermal interface" in section["reason"]
    dt.write_atomic(cfg, section)
    data = json.loads(cfg.read_text())
    assert data["cpu_model"] == "x" and data["thermal"] == {"paths": {}}
    assert data["thermal_sensors"]["catalog"] == []


def test_conformance_kit_requires_read_with_describe():
    from alems_sdk._kit_families import MeasurementKit

    class _DescribesOnly(_OldReader):
        def describe_sensors(self):
            return []

    class _Both(_DescribesOnly):
        def read_sensors(self):
            return {}
    msgs = []
    kit = MeasurementKit()
    kit.fail = lambda m: msgs.append(m)
    kit._check_thermal_catalog(_DescribesOnly)
    kit._check_thermal_catalog(_Both)
    kit._check_thermal_catalog(_OldReader)            # old readers are untouched
    assert len(msgs) == 1 and "read_sensors" in msgs[0]
