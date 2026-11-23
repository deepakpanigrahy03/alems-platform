"""
tests/test_arm_thermal_zone_order.py

Regression test for numeric thermal zone ordering in ARMThermalReader.

Zone names are assigned in discovery order (acpitz, acpitz_1, acpitz_2, ...).
A text sort puts thermal_zone10 before thermal_zone2, which attaches names
to the wrong sensors on machines with 10 or more zones. Discovery must sort
by the zone number.
"""

import glob as real_glob_module

import core.readers.arm_thermal_reader as mod
from core.readers.arm_thermal_reader import ARMThermalReader, _zone_sort_key

# Keep a reference to the real glob before the test patches it.
_real_glob = real_glob_module.glob


def test_zone_sort_key_is_numeric():
    """Zones 0 to 11 sort as numbers, not text."""
    paths = ["/sys/class/thermal/thermal_zone%d/" % i for i in (10, 2, 11, 0, 1, 9)]
    ordered = sorted(paths, key=_zone_sort_key)
    assert [p.rstrip("/").rsplit("thermal_zone", 1)[1] for p in ordered] == \
        ["0", "1", "2", "9", "10", "11"]


def test_zone_without_number_sorts_last():
    """An unexpected folder name never breaks discovery; it sorts last."""
    paths = ["/x/thermal_zone3/", "/x/thermal_zoneX/", "/x/thermal_zone1/"]
    assert sorted(paths, key=_zone_sort_key)[-1] == "/x/thermal_zoneX/"


def test_discovery_assigns_names_in_numeric_order(tmp_path, monkeypatch):
    """With 12 acpitz zones, acpitz_2 is zone 2 and acpitz_10 is zone 10."""
    for i in range(12):
        zone = tmp_path / ("thermal_zone%d" % i)
        zone.mkdir()
        (zone / "type").write_text("acpitz\n")
        (zone / "temp").write_text("45000\n")
    # Point discovery at the fake sysfs tree instead of /sys/class/thermal.
    monkeypatch.setattr(mod.glob, "glob",
                        lambda pattern: _real_glob(str(tmp_path) + "/thermal_zone*/"))
    reader = ARMThermalReader.__new__(ARMThermalReader)
    zones = reader._discover_zones()
    assert zones["acpitz"].endswith("thermal_zone0/temp")
    assert zones["acpitz_2"].endswith("thermal_zone2/temp")
    assert zones["acpitz_10"].endswith("thermal_zone10/temp")
    assert zones["acpitz_11"].endswith("thermal_zone11/temp")
