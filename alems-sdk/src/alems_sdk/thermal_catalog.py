"""
Thermal sensor catalog contract (A-LEMS SDK).

A thermal reader can describe the sensors it reads, so that sensor facts
(identity, interface, reported format, interface resolution, limits) are
discovered from the machine instead of typed by hand. Every descriptor is
a plain dict validated here; the same schema is used on every platform.

Two kinds:
  sensor   one physical measurement point as the interface reports it
  derived  a value computed from sensors (average, maximum) or a legacy
           reader key whose origin is unknown; never presented as a sensor

Facts and observations stay apart: interface_resolution_c is what the
interface format implies (sysfs millidegrees -> 0.001); the step actually
seen in data is a probe observation (thermal_probe.py), never stored here.
Missing values are None, never 0.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

DESCRIPTOR_SCHEMA_VERSION = 2      # 2: scope added

KINDS = ("sensor", "derived")
# what a value measures: a die maximum and a package sensor are different quantities
SCOPES = ("die", "core", "package", "zone", "gpu", "battery", "board", "storage",
          "network", "unknown")
# reading validity range (same as the zone readers): outside is OUT_OF_RANGE, kept
VALID_MIN_C, VALID_MAX_C = -10.0, 125.0
QUALITY = ("VALID", "OUT_OF_RANGE", "READ_FAILED", "UNAVAILABLE")
# |value| below this on a powered machine is an unused slot or a stub, not a
# temperature (x86 SEN4 reads 0.05 C, GB10 spbm dla 0 C); stored OUT_OF_RANGE
NEAR_ZERO_C = 1.0
CONFIDENCE = ("strong", "inferred", "ambiguous")
REQUIRED = ("id", "kind", "quantity", "unit", "interface", "reported_format",
            "identity_key", "identity_confidence", "read_by", "scope")

# fields that describe the machine; volatile values never enter the hash
_HASHED = REQUIRED + ("interface_resolution_c", "limits", "source_ref", "device",
                      "driver", "label", "derived_from", "formula")


def descriptor(id: str, kind: str, interface: str, reported_format: str,
               identity_key: str, identity_confidence: str, read_by: Any,
               interface_resolution_c: Optional[float] = None,
               limits: Optional[List[Dict[str, Any]]] = None,
               source_ref: Optional[str] = None, device: Optional[str] = None,
               driver: Optional[str] = None, label: Optional[str] = None,
               derived_from: Optional[List[str]] = None, formula: Optional[str] = None,
               notes: Optional[str] = None, scope: str = "unknown") -> Dict[str, Any]:
    """Build one temperature descriptor (validated by validate_descriptor)."""
    return {"id": id, "kind": kind, "quantity": "temperature", "unit": "C", "scope": scope,
            "interface": interface, "reported_format": reported_format,
            "interface_resolution_c": interface_resolution_c,
            "limits": list(limits or []), "source_ref": source_ref, "device": device,
            "driver": driver, "label": label, "identity_key": identity_key,
            "identity_confidence": identity_confidence, "read_by": read_by,
            "derived_from": list(derived_from or []), "formula": formula, "notes": notes}


def limit(name: str, kind: str, temp_c: Optional[float], source: str) -> Dict[str, Any]:
    """One limit (trip point or threshold); temp_c None = advertised but unreadable."""
    return {"name": name, "type": kind, "temp_c": temp_c, "source": source}


def legacy_descriptors(reader_name: str, keys: Iterable[str]) -> List[Dict[str, Any]]:
    """
    Default for readers that do not describe themselves: every key of
    read_all_thermal() is 'derived' with an unknown formula, because an
    old reader may return aggregates; it is never presented as a sensor.
    """
    return [descriptor("%s:%s" % (reader_name, k), "derived", "legacy_reader", "unknown",
                       "%s:%s" % (reader_name, k), "ambiguous", reader_name,
                       formula="unknown (legacy reader key)") for k in sorted(keys)]


def validate_descriptor(d: Dict[str, Any]) -> List[str]:
    """Problems with one descriptor (empty list = valid)."""
    errs = ["missing %s" % k for k in REQUIRED if d.get(k) in (None, "")]
    if d.get("kind") not in KINDS:
        errs.append("kind must be one of %s" % (KINDS,))
    if d.get("identity_confidence") not in CONFIDENCE:
        errs.append("identity_confidence must be one of %s" % (CONFIDENCE,))
    if d.get("scope") not in SCOPES:
        errs.append("scope must be one of %s" % (SCOPES,))
    res = d.get("interface_resolution_c")
    if res is not None and (not isinstance(res, (int, float)) or res <= 0):
        errs.append("interface_resolution_c must be > 0 or None (never 0)")
    if d.get("kind") == "derived" and not d.get("formula"):
        errs.append("derived descriptor needs a formula (or 'unknown ...')")
    for lim in d.get("limits") or []:
        if lim.get("temp_c") == 0:
            errs.append("limit %s: 0 is not a missing value; use None" % lim.get("name"))
    return errs


def catalog_sha256(descriptors: List[Dict[str, Any]]) -> str:
    """Deterministic hash of the catalog: hashed fields only, sorted by id."""
    canon = [{k: d.get(k) for k in _HASHED} for d in sorted(descriptors, key=lambda x: x["id"])]
    blob = json.dumps({"schema": DESCRIPTOR_SCHEMA_VERSION, "sensors": canon},
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def expected_ids(descriptors: List[Dict[str, Any]], reader_name: str,
                 kinds: tuple = ("sensor",)) -> List[str]:
    """
    Catalog ids a reader records (read_by names it); the run's expected sensor set.

    Only real sensors by default: a derived value is recomputed from its inputs
    with the formula kept in the catalog, so storing it would only duplicate them.
    """
    def _by(d):
        rb = d.get("read_by")
        return reader_name in (rb if isinstance(rb, list) else [rb])
    return sorted(d["id"] for d in descriptors if _by(d) and d.get("kind") in kinds)


def sensor_quality(values: Dict[str, Optional[float]], expected: List[str],
                   lo: float = VALID_MIN_C, hi: float = VALID_MAX_C):
    """
    Values and quality for every expected sensor of one read.

    Returns ({id: C or None}, {id: quality}). Absent from the read: UNAVAILABLE;
    read but None: READ_FAILED; outside [lo, hi]: OUT_OF_RANGE (value kept for
    audit); otherwise VALID. Every expected id gets a quality word, so a
    missing entry can never be taken for VALID.
    """
    vals, qual = {}, {}
    for sid in expected:
        if sid not in values:
            vals[sid], qual[sid] = None, "UNAVAILABLE"
        elif values[sid] is None:
            vals[sid], qual[sid] = None, "READ_FAILED"
        else:
            v = float(values[sid])
            ok = lo <= v <= hi and abs(v) >= NEAR_ZERO_C
            vals[sid], qual[sid] = v, ("VALID" if ok else "OUT_OF_RANGE")
    return vals, qual
