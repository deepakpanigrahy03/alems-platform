#!/usr/bin/env python3
"""
detect_thermal.py: discover and measure the temperature sensors of this machine.

Runs at install (after detect_hardware.py, inside the venv) and on demand.
It asks the platform's own thermal readers which sensors they read
(describe_sensors), adds sensors no reader uses yet (Linux hwmon, extra
Mac sensors) marked read_by: discovery_utility, then probes all of them
for a short idle period and writes the result to config/hw_config.json
under the top-level key "thermal_sensors" (detect_hardware.py never
replaces that key). Read-only towards hardware: no sudo, no fan or clock
change, no process is stopped.

What it writes:
  catalog          discovered facts per sensor (identity, interface, format,
                   interface resolution, limits, which reader records it)
  catalog_sha256   identity of the catalog (facts only, no measurements)
  measurements     probe observations per sensor (observed step and status,
                   value-change intervals, min/median/max)
  sources          read latency and failures per reader
  probe            duration achieved, rounds, interruptions
  provenance       time, kernel, platform, GPU driver, load before and after

Usage:
  python scripts/detect_thermal.py                 # 60 s probe, write hw_config.json
  python scripts/detect_thermal.py --dry-run       # print summary, write nothing
  python scripts/detect_thermal.py --seconds 120 --hz 50
Exit code is 0 unless the config cannot be written; no sensor found is not an error.
"""

import argparse
import json
import logging
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alems_sdk import thermal_probe                                   # noqa: E402
from alems_sdk.thermal_catalog import (DESCRIPTOR_SCHEMA_VERSION,     # noqa: E402
                                       catalog_sha256, validate_descriptor)

logger = logging.getLogger("detect_thermal")
KEY = "thermal_sensors"
Source = Tuple[str, List[Dict[str, Any]], Callable[[], Dict[str, Optional[float]]]]


def _zone_source() -> Optional[Source]:
    """Linux thermal zones through ThermalReaderV2 (what runs and recordings store)."""
    from core.thermal.thermal_descriptors import list_zones, zone_descriptors
    from core.thermal.thermal_reader_v2 import ThermalReaderV2
    zones = list_zones()
    if not zones:
        return None
    reg = {z["index"]: {"zone_id": z["index"], "zone_type": z["type"], "live_path": z["dir"]}
           for z in zones}
    v2 = ThermalReaderV2(reg)
    return "ThermalReaderV2", zone_descriptors("ThermalReaderV2", zones), v2.read_sensors


def _registry_source(cfg: Dict) -> Optional[Source]:
    """The platform thermal reader the registry selects for runs."""
    from core.readers.bootstrap import register_all_readers
    from core.readers.factory import ReaderFactory
    register_all_readers()
    reader = ReaderFactory.get_thermal_reader(cfg)
    # describe whatever the reader can see, even if its availability flag is
    # narrower (the Mac reader's flag only checks the battery)
    if not reader.describe_sensors():
        return None
    read = getattr(reader, "read_discovery_sensors", reader.read_sensors)
    return reader.get_name(), reader.describe_sensors(), read


def _gpu_source() -> Optional[Source]:
    """GPU temperature and thresholds through the NVML backend."""
    from core.readers.gpu_collector import NVMLBackend
    nv = NVMLBackend()
    if not nv.is_available():
        return None
    return "NVMLBackend", nv.describe_temperature_sensors(), nv.read_temperature_sensors


def _hwmon_source() -> Optional[Source]:
    """Linux hwmon inputs; read here only (read_by discovery_utility)."""
    from core.thermal.thermal_descriptors import hwmon_descriptors, read_hwmon_c
    descs = hwmon_descriptors()
    if not descs:
        return None
    paths = {d["id"]: d["source_ref"] for d in descs}
    return "hwmon", descs, lambda: {i: read_hwmon_c(p) for i, p in paths.items()}


def collect_sources(cfg: Dict) -> Tuple[List[Source], List[str]]:
    """Every available source; a source that fails is reported, never fatal."""
    out, problems = [], []
    for name, build in (("zones", _zone_source), ("registry", lambda: _registry_source(cfg)),
                        ("gpu", _gpu_source), ("hwmon", _hwmon_source)):
        try:
            src = build()
        except Exception as exc:
            problems.append("%s: %s: %s" % (name, type(exc).__name__, str(exc)[:160]))
            continue
        if src is not None:
            out.append(src)
    return out, problems


def merge_catalog(sources: List[Source]) -> Tuple[List[Dict], Dict[str, Callable], List[str]]:
    """
    One descriptor per id. A sensor described by several readers keeps the
    first descriptor and lists every reader in read_by; each id is probed
    through the first source only, so it is read once per round.
    """
    catalog: Dict[str, Dict] = {}
    owner: Dict[str, str] = {}
    invalid = []
    for name, descs, _read in sources:
        for d in descs:
            invalid += ["%s: %s" % (d.get("id"), e) for e in validate_descriptor(d)]
            if d["id"] in catalog:
                catalog[d["id"]]["read_by"] = _union(catalog[d["id"]]["read_by"], d["read_by"])
                continue
            catalog[d["id"]], owner[d["id"]] = dict(d), name
    rounds = {name: _filtered(read, {i for i, o in owner.items() if o == name})
              for name, _descs, read in sources}
    return [catalog[k] for k in sorted(catalog)], rounds, invalid


def _union(a: Any, b: Any) -> List[str]:
    """read_by of a sensor described by several readers."""
    seen = []
    for x in (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b]):
        if x not in seen:
            seen.append(x)
    return seen


def _filtered(read: Callable, ids: set) -> Callable[[], Dict[str, Optional[float]]]:
    """Read one source and keep only the ids it owns in the catalog."""
    return lambda: {k: v for k, v in (read() or {}).items() if k in ids}


def conditions() -> Dict[str, Any]:
    """Load and GPU use at this moment (were the measurements taken at idle?)."""
    load = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    return {"loadavg_1m": load[0], "gpu_util_pct": _gpu_util()}


def _gpu_util() -> Optional[float]:
    """GPU 0 utilisation in percent, or None without NVML."""
    try:
        import pynvml
        pynvml.nvmlInit()
        return float(pynvml.nvmlDeviceGetUtilizationRates(pynvml.nvmlDeviceGetHandleByIndex(0)).gpu)
    except Exception:
        return None


def provenance(before: Dict, after: Dict) -> Dict[str, Any]:
    """When, where and under which software the probe ran (never a hostname)."""
    return {"probed_at_utc": datetime.now(timezone.utc).isoformat(),
            "kernel": platform.release(), "system": platform.system(),
            "machine": platform.machine(), "python": platform.python_version(),
            "gpu_driver": _nvml_text("nvmlSystemGetDriverVersion"),
            "nvml_version": _nvml_text("nvmlSystemGetNVMLVersion"),
            "conditions_before": before, "conditions_after": after}


def _nvml_text(fn: str) -> Optional[str]:
    """One NVML system string, or None."""
    try:
        import pynvml
        pynvml.nvmlInit()
        v = getattr(pynvml, fn)()
        return v.decode() if isinstance(v, bytes) else str(v)
    except Exception:
        return None


def build(cfg: Dict, seconds: float, hz: float) -> Dict[str, Any]:
    """Discover, probe and assemble the thermal_sensors section."""
    sources, problems = collect_sources(cfg)
    catalog, rounds, invalid = merge_catalog(sources)
    logger.info("thermal sensors: %d described by %s", len(catalog), [s[0] for s in sources])
    before = conditions()
    result = thermal_probe.probe(rounds, seconds, hz) if catalog else \
        {"sensors": {}, "sources": {}, "probe": None}
    return {"schema_version": DESCRIPTOR_SCHEMA_VERSION, "status": "ok" if catalog else "no_sensors",
            "reason": None if catalog else _no_sensor_reason(problems),
            "catalog_sha256": catalog_sha256(catalog), "catalog": catalog,
            "measurements": result["sensors"], "sources": result["sources"],
            "probe": result["probe"], "provenance": provenance(before, conditions()),
            "source_problems": problems, "invalid_descriptors": invalid}


def _no_sensor_reason(problems: List[str]) -> str:
    """Why nothing was found (does not claim the machine has no sensors)."""
    base = "no supported thermal interface on this platform (%s)" % platform.system()
    return base + ("; " + "; ".join(problems) if problems else "")


def write_atomic(path: Path, section: Dict) -> None:
    """Merge the section into hw_config.json without touching other keys; atomic replace."""
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg[KEY] = section
    fd, tmp = tempfile.mkstemp(prefix=".hw_config.", dir=str(path.parent))
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh, indent=2, default=str)
    os.replace(tmp, path)


def summary(section: Dict) -> str:
    """Short table for the console."""
    lines = ["status %s, %d sensors, catalog %s" % (section["status"], len(section["catalog"]),
                                                    section["catalog_sha256"][:12])]
    for d in section["catalog"]:
        m = section["measurements"].get(d["id"], {})
        lines.append("  %-44s %-8s read_by=%-28s step=%-8s %s" % (
            d["id"][:44], d["kind"], str(d["read_by"])[:28], m.get("observed_value_step_c"),
            m.get("resolution_status")))
    dups = {k: v["possible_duplicate_of"] for k, v in section["measurements"].items()
            if v.get("possible_duplicate_of")}
    lines += ["  possible duplicate (advisory): %s = %s" % (k, ", ".join(v)) for k, v in sorted(dups.items())]
    lines += ["  SOURCE PROBLEM: %s" % p for p in section["source_problems"]]
    lines += ["  INVALID DESCRIPTOR: %s" % p for p in section["invalid_descriptors"]]
    return "\n".join(lines)


def main() -> int:
    """Command line entry; see module docstring."""
    ap = argparse.ArgumentParser(description="Discover and measure temperature sensors")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--config", default=str(ROOT / "config" / "hw_config.json"))
    ap.add_argument("--dry-run", action="store_true", help="print the result, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = Path(args.config)
    cfg = json.loads(path.read_text()) if path.exists() else {}
    section = build(cfg, args.seconds, args.hz)
    print(summary(section))
    if args.dry_run:
        return 0
    try:
        write_atomic(path, section)
    except OSError as exc:
        logger.error("could not write %s: %s", path, exc)
        return 1
    logger.info("thermal sensors written to %s (%s)", path, KEY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
