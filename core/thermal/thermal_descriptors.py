"""
Sensor descriptors for Linux sysfs thermal sources (core).

Builds the SDK thermal catalog descriptors (alems_sdk.thermal_catalog) for
thermal zones and hwmon temperature inputs, from what the kernel exposes:
type, label, device link, driver and every trip point or limit file.
Shared by the Linux thermal readers and by scripts/detect_thermal.py, so
one sensor gets one id whichever code describes it.

Nothing here is guessed: a limit file that is absent is not listed, one
that cannot be parsed is listed with temp_c None, and identity confidence
says how sure the name is across reboots (zone numbers can change).
"""

from __future__ import annotations

import glob
import os
from typing import Dict, List, Optional

from alems_sdk.thermal_catalog import descriptor, limit

SYSFS_THERMAL = "/sys/class/thermal"
SYSFS_HWMON = "/sys/class/hwmon"
MILLIDEGREE_C = 0.001          # sysfs reports temperatures in millidegrees C
_HWMON_LIMITS = (("crit", "critical"), ("emergency", "emergency"),
                 ("max", "max"), ("min", "min"), ("crit_hyst", "critical_hysteresis"))


def _read(path: str) -> Optional[str]:
    """Stripped file content, or None when absent or unreadable."""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _millideg(raw: Optional[str]) -> Optional[float]:
    """Millidegrees text to C; None when absent or not a number."""
    try:
        return int(raw) / 1000.0 if raw is not None else None
    except ValueError:
        return None


def _device(path: str) -> Optional[str]:
    """Resolved device link of a sysfs node, or None."""
    link = os.path.join(path, "device")
    return os.path.realpath(link) if os.path.exists(link) else None


def zone_id(index: int, zone_type: str) -> str:
    """Catalog id of a thermal zone (index and type, as stored data uses them)."""
    return "thermal_zone:%d:%s" % (index, zone_type)


def zone_trip_points(zone_dir: str) -> List[Dict]:
    """Every trip_point_N of a zone with its type; unparsable values are None."""
    out = []
    for tfile in sorted(glob.glob(os.path.join(zone_dir, "trip_point_*_temp")),
                        key=lambda p: int(os.path.basename(p).split("_")[2])):
        n = os.path.basename(tfile).split("_")[2]
        kind = _read(os.path.join(zone_dir, "trip_point_%s_type" % n)) or "unknown"
        out.append(limit("trip_point_%s" % n, kind, _millideg(_read(tfile)), "sysfs"))
    return out


def zone_descriptors(read_by: str, zones: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Descriptors for thermal zones.

    Args:
        read_by: reader that records these zones.
        zones:   [{"index", "type", "dir"}]; default: every zone on this machine.
    """
    zones = zones if zones is not None else list_zones()
    type_count: Dict[str, int] = {}
    for z in zones:
        type_count[z["type"]] = type_count.get(z["type"], 0) + 1
    return [_zone_descriptor(z, read_by, type_count[z["type"]] == 1) for z in zones]


def _zone_descriptor(z: Dict, read_by: str, type_unique: bool) -> Dict:
    """One zone: identity is strong with a device link, inferred if its type is unique."""
    dev = _device(z["dir"])
    driver = os.path.basename(os.path.realpath(os.path.join(dev, "driver"))) \
        if dev and os.path.exists(os.path.join(dev, "driver")) else None
    conf = "strong" if dev else ("inferred" if type_unique else "ambiguous")
    key = "%s@%s" % (z["type"], dev) if dev else "%s#%d" % (z["type"], z["index"])
    return descriptor(zone_id(z["index"], z["type"]), "sensor", "sysfs_thermal_zone",
                      "millidegree_c", key, conf, read_by,
                      interface_resolution_c=MILLIDEGREE_C,
                      limits=zone_trip_points(z["dir"]),
                      source_ref=os.path.join(z["dir"], "temp"), device=dev, driver=driver,
                      label=z["type"])


def list_zones() -> List[Dict]:
    """Readable thermal zones: [{"index", "type", "dir"}] in numeric order."""
    out = []
    for d in glob.glob(os.path.join(SYSFS_THERMAL, "thermal_zone*")):
        ztype, idx = _read(os.path.join(d, "type")), os.path.basename(d)[len("thermal_zone"):]
        if ztype and idx.isdigit() and os.path.exists(os.path.join(d, "temp")):
            out.append({"index": int(idx), "type": ztype, "dir": d})
    return sorted(out, key=lambda z: z["index"])


def read_zone_c(zone_dir: str) -> Optional[float]:
    """Current temperature of one zone in C, or None."""
    return _millideg(_read(os.path.join(zone_dir, "temp")))


def hwmon_inputs() -> List[Dict]:
    """Every hwmon tempN_input: [{"id", "path", "name", "label", "device", "limits"}]."""
    out = []
    for hdir in sorted(glob.glob(os.path.join(SYSFS_HWMON, "hwmon*"))):
        name, dev = _read(os.path.join(hdir, "name")) or "unknown", _device(hdir)
        for inp in sorted(glob.glob(os.path.join(hdir, "temp*_input"))):
            out.append(_hwmon_input(hdir, inp, name, dev))
    return out


def _hwmon_input(hdir: str, inp: str, name: str, dev: Optional[str]) -> Dict:
    """One hwmon temperature input with its label and limit files."""
    base = os.path.basename(inp)[:-len("_input")]                 # e.g. temp2
    label = _read(os.path.join(hdir, base + "_label"))
    lims = [limit(base + "_" + suf, kind, _millideg(_read(os.path.join(hdir, "%s_%s" % (base, suf)))),
                  "sysfs") for suf, kind in _HWMON_LIMITS
            if os.path.exists(os.path.join(hdir, "%s_%s" % (base, suf)))]
    where = os.path.basename(dev) if dev else os.path.basename(hdir)
    return {"id": "hwmon:%s:%s@%s" % (name, label or base, where), "path": inp, "name": name,
            "label": label or base, "device": dev, "limits": lims}


def hwmon_descriptors(read_by: str = "discovery_utility") -> List[Dict]:
    """Descriptors for hwmon inputs (default: read only by discovery, not recorded)."""
    return [descriptor(h["id"], "sensor", "sysfs_hwmon", "millidegree_c",
                       "%s:%s@%s" % (h["name"], h["label"], h["device"]),
                       "strong" if h["device"] else "inferred", read_by,
                       interface_resolution_c=MILLIDEGREE_C, limits=h["limits"],
                       source_ref=h["path"], device=h["device"], driver=h["name"],
                       label=h["label"]) for h in hwmon_inputs()]


def read_hwmon_c(path: str) -> Optional[float]:
    """Current value of one hwmon input in C, or None."""
    return _millideg(_read(path))
