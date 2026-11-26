"""
Tests for the thermal_exposure machine profile loader, ambient reader and
value-change probe (spec tests 1, 5, 6 and the probe contract).

Run (repo root, venv active):
    python3 -m pytest -q tests/test_thermal_exposure_profile.py

Uses an in-memory SQLite database built from the real e001 migration plus
minimal catalog tables; never opens a store.
"""

import copy
import sqlite3
from pathlib import Path

import pytest
import yaml

from extensions.thermal_exposure import profile_loader as pl
from extensions.thermal_exposure.ambient_reader import AmbientReader
from extensions.thermal_exposure.value_change_probe import observe_value_changes

ROOT = Path(__file__).resolve().parents[1]
E001 = ROOT / "migrations" / "extensions" / "thermal_exposure" / "e001_create_thermal_exposure.sql"
E004 = ROOT / "migrations" / "extensions" / "thermal_exposure" / "e004_sensor_resolution_provenance.sql"
# discovered facts as scripts/detect_thermal.py writes them on GB10
FACTS = {"sha": "cat-sha", "facts": dict(
    {"thermal_zone:%d:acpitz" % i: {"interface_resolution_c": 0.001, "observed_step_c": 0.1,
                                    "observed_status": "measured"} for i in range(7)},
    **{"nvml:gpu0:temperature": {"interface_resolution_c": 1.0, "observed_step_c": None,
                                 "observed_status": "insufficient_transitions"}})}
PROFILE = ROOT / "config" / "machine_profiles" / "gpu_soc_a.yaml"
RAILS = ["dc_input", "sys_total", "soc_pkg", "cpu_gpu", "cpu_p", "cpu_e", "vcore", "gpu", "prereg", "dla"]


def _catalog(gpu=True):
    """Fake discovered catalog matching GB10 (7 acpitz zones, 10 rails)."""
    return {"rails": {n: i + 1 for i, n in enumerate(RAILS)},
            "domains": {"GPU": (7, "uj", 1)},
            "zones": {("acpitz", i): "SOC" for i in range(7)},
            "gpu": gpu, "sensor_facts": FACTS}


@pytest.fixture
def profile():
    with open(PROFILE) as fh:
        return yaml.safe_load(fh)


@pytest.fixture(autouse=True)
def readable_zones(monkeypatch):
    """Zone temp files count as readable unless a test says otherwise."""
    monkeypatch.setattr(pl.os, "access", lambda path, mode: True)
    monkeypatch.setattr(pl, "load_sensor_facts", lambda path=None: FACTS)
    monkeypatch.setattr(pl, "load_sensor_facts", lambda path=None: FACTS)


def _reject(profile, cat=None, match=""):
    with pytest.raises(pl.ProfileError, match=match):
        pl.build_rows(profile, "sha", cat or _catalog())


# ---------------------------------------------------------------- valid profile
def test_valid_profile_builds_rows(profile):
    crow, srow = pl.build_rows(profile, "sha", _catalog())
    assert len(crow) == 10 and len(srow) == 8
    by_name = {r[2]: r for r in crow}
    assert by_name["gpu"][12] == 1                      # main channel flagged
    assert by_name["dc_input"][11] == 0                 # boundary NULL -> not eligible
    assert by_name["vcore"][11] == 0                    # overlapping -> not eligible
    assert {r[2] for r in srow if r[14] == 1} == {"gpu_temp"}


# ---------------------------------------------------------------- each invalid case
def test_duplicate_channel_name_rejected(profile):
    profile["channels"].append(copy.deepcopy(profile["channels"][0]))
    _reject(profile, match="duplicate channel name")


def test_duplicate_sensor_name_rejected(profile):
    profile["sensors"].append(copy.deepcopy(profile["sensors"][0]))
    _reject(profile, match="duplicate sensor name")


def test_unknown_source_table_rejected(profile):
    profile["channels"][0]["source_table"] = "made_up_samples"
    _reject(profile, match="unknown source table")


def test_unknown_source_key_rejected(profile):
    profile["channels"][0]["source_key"] = "no_such_rail"
    _reject(profile, match="unknown rail")


def test_invalid_unit_rejected(profile):
    profile["channels"][0]["unit"] = "W"
    _reject(profile, match="invalid unit")


def test_gpu_power_from_gpu_samples_rejected(profile):
    ch = profile["channels"][6]
    ch.update(source_table="gpu_samples", source_key="0", unit="uJ", measurement_kind="power_reading")
    _reject(profile, match="measurement_kind")


def test_main_channel_not_listed_rejected(profile):
    profile["main_channel"] = "nope"
    _reject(profile, match="main_channel")


def test_main_sensor_not_listed_rejected(profile):
    profile["main_sensor"] = "nope"
    _reject(profile, match="main_sensor")


def test_no_usable_reader_rejected(profile):
    cat = _catalog(gpu=False)
    cat["rails"] = {}                                   # machine without power rails (x86)
    _reject(profile, cat, match="no usable reader")


def test_zone_not_discovered_rejected(profile):
    profile["sensors"][0]["zone_index"] = 42
    _reject(profile, match="not discovered")


def test_unreadable_zone_rejected(profile, monkeypatch):
    monkeypatch.setattr(pl.os, "access", lambda path, mode: False)
    _reject(profile, match="no readable temp file")


# ---------------------------------------------------------------- write through a handle
def _store():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE runs (run_id INTEGER PRIMARY KEY);
        CREATE TABLE power_rails (rail_id INTEGER PRIMARY KEY, rail_name TEXT);
        CREATE TABLE energy_domains (domain_id INTEGER PRIMARY KEY, name TEXT, unit TEXT, is_cumulative INTEGER);
        CREATE TABLE thermal_zones (zone_id INTEGER PRIMARY KEY, machine_id TEXT, zone_type TEXT,
                                    zone_index INTEGER, canonical_role TEXT, active INTEGER);
    """)
    con.executescript(E001.read_text())
    con.executescript(E004.read_text())
    con.executemany("INSERT INTO power_rails VALUES (?, ?)", [(i + 1, n) for i, n in enumerate(RAILS)])
    con.executemany("INSERT INTO thermal_zones VALUES (NULL, 'm', 'acpitz', ?, 'SOC', 1)", [(i,) for i in range(7)])
    return con


def test_rows_written_match_profile_and_reload_is_idempotent(monkeypatch):
    monkeypatch.setattr(pl, "_gpu_reader_available", lambda: True)
    con = _store()
    assert pl.load_into_store(con, str(PROFILE), "m") == (10, 8)
    assert pl.load_into_store(con, str(PROFILE), "m") == (10, 8)
    names = {r[0] for r in con.execute("SELECT channel_name FROM te_channel")}
    assert names == set(RAILS)
    assert con.execute("SELECT COUNT(*) FROM te_sensor").fetchone()[0] == 8
    assert con.execute("SELECT COUNT(*) FROM te_channel WHERE source_key = 'gpu'").fetchone()[0] == 0


def test_no_hostname_stored(monkeypatch):
    monkeypatch.setattr(pl, "_gpu_reader_available", lambda: True)
    con = _store()
    pl.load_into_store(con, str(PROFILE), "m")
    for table in ("te_channel", "te_sensor"):
        rows = con.execute("SELECT * FROM %s" % table).fetchall()
        assert all("m" != v for row in rows for v in row)


# ---------------------------------------------------------------- ambient (spec test 6)
def test_ambient_without_probe_is_null_and_unavailable():
    reading = AmbientReader().read()
    assert reading["temp_celsius"] is None
    assert reading["quality_flag"] == "UNAVAILABLE"
    con = _store()
    con.execute("INSERT INTO runs VALUES (1)")
    con.execute("INSERT INTO te_ambient_samples (run_id, sensor_name, timestamp_ns, temp_celsius,"
                " quality_flag, invalid_reason) VALUES (1, ?, ?, ?, ?, ?)",
                (reading["sensor_name"], reading["timestamp_ns"], reading["temp_celsius"],
                 reading["quality_flag"], reading["invalid_reason"]))
    assert con.execute("SELECT temp_celsius, quality_flag FROM te_ambient_samples").fetchone() == (None, "UNAVAILABLE")


def test_ambient_zero_cannot_be_unavailable():
    con = _store()
    con.execute("INSERT INTO runs VALUES (1)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO te_ambient_samples (run_id, sensor_name, timestamp_ns, temp_celsius,"
                    " quality_flag) VALUES (1, 'ambient', 1, 0.0, 'UNAVAILABLE')")


# ---------------------------------------------------------------- probe contract
def test_probe_counts_changes_and_keeps_repeats():
    values = iter([20.0, 20.0, 20.0, 21.0, 21.0, 22.0] + [22.0] * 1000)
    ticks = iter(range(10_000))
    out = observe_value_changes({"z": lambda: next(values)}, 6.0, {"z": 0.001},
                                clock=lambda: float(next(ticks)) / 3.0)
    z = out["z"]
    assert z["n_value_changes"] == 2
    assert z["n_reads"] >= 6                            # repeats counted, not dropped
    assert z["observed_value_change_interval_s"] is not None
    assert z["evidence_only"] is True


# ---------------------------------------------------------------- discovered resolution
def _sensor_rows(profile, cat):
    """te_sensor rows by sensor name."""
    _c, srow = pl.build_rows(profile, "sha", cat)
    return {r[2]: r for r in srow}


def test_resolution_comes_from_catalog_with_its_origin(profile):
    rows = _sensor_rows(profile, _catalog())
    zone, gpu = rows["soc_zone_0"], rows["gpu_temp"]
    assert (zone[9], zone[16], zone[17], zone[18], zone[19]) == (0.1, 0.001, 0.1, "measured", "cat-sha")
    assert (gpu[9], gpu[17], gpu[18]) == (1.0, None, "interface_only")    # no step invented


def test_typed_resolution_that_disagrees_is_rejected(profile):
    p = copy.deepcopy(profile)
    p["sensors"][0]["nominal_resolution_c"] = 0.001
    _reject(p, match="disagrees with discovered")


def test_no_catalog_and_no_typed_value_is_rejected(profile):
    cat = dict(_catalog(), sensor_facts={"sha": None, "facts": {}})
    _reject(profile, cat=cat, match="run scripts/detect_thermal.py")


def test_typed_value_without_catalog_is_marked_unverified(profile):
    p = copy.deepcopy(profile)
    for s in p["sensors"]:
        s["nominal_resolution_c"] = 0.1 if s["source_table"] == "thermal_samples_v2" else 1.0
    rows = _sensor_rows(p, dict(_catalog(), sensor_facts={"sha": None, "facts": {}}))
    assert {r[18] for r in rows.values()} == {"typed_unverified"}


def test_auto_profile_follows_platform_class(tmp_path, monkeypatch):
    from extensions.thermal_exposure import extension as ext
    (tmp_path / "config" / "machine_profiles").mkdir(parents=True)
    (tmp_path / "config" / "hw_config.json").write_text('{"platform_class": "nvidia_grace"}')
    (tmp_path / "config" / "machine_profiles" / "index.yaml").write_text(
        "profiles:\n  nvidia_grace: gpu_soc_a\n")
    monkeypatch.setattr(ext, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(ext, "_PROFILE_DIR", tmp_path / "config" / "machine_profiles")
    assert ext._auto_profile() == "gpu_soc_a"
    (tmp_path / "config" / "hw_config.json").write_text('{"platform_class": "apple_silicon"}')
    assert ext._auto_profile() is None                      # never another platform's profile
