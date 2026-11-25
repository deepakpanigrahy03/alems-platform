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


# ── Signal catalog: thermal-response signals (not temperatures) ─────────────
SIGNAL_SCHEMA_VERSION = 1
SIGNAL_KINDS = ("level", "category", "counter", "bitmask", "setting")
# what is known about a signal's meaning; a VALID read says only that the read worked
INTERPRETATION = ("validated", "empirical", "unverified", "invalidated")
SIGNAL_REQUIRED = ("id", "kind", "source", "source_field", "raw_unit", "interpretation",
                   "analysis_enabled", "read_by")
_INT64_MAX = 2 ** 63 - 1


def signal_descriptor(id: str, kind: str, source: str, source_field: str, raw_unit: str,
                      interpretation: str, analysis_enabled: bool, read_by: str,
                      decoder: Optional[Dict[str, str]] = None, decoder_version: Optional[str] = None,
                      device: Optional[str] = None, scope: str = "unknown",
                      notes: Optional[str] = None) -> Dict[str, Any]:
    """
    Build one signal descriptor (validated by validate_signal).

    analysis_enabled False keeps the raw values out of default analysis, for
    signals whose meaning is unverified or contradicted by measurement.
    """
    return {"id": id, "kind": kind, "source": source, "source_field": source_field,
            "raw_unit": raw_unit, "interpretation": interpretation,
            "analysis_enabled": bool(analysis_enabled) and interpretation in ("validated", "empirical"),
            "read_by": read_by, "decoder": decoder, "decoder_version": decoder_version,
            "device": device, "scope": scope, "notes": notes}


def validate_signal(d: Dict[str, Any]) -> List[str]:
    """Problems with one signal descriptor; [] when it is valid."""
    errs = ["missing %s" % k for k in SIGNAL_REQUIRED if d.get(k) is None]
    if d.get("kind") not in SIGNAL_KINDS:
        errs.append("kind must be one of %s" % (SIGNAL_KINDS,))
    if d.get("interpretation") not in INTERPRETATION:
        errs.append("interpretation must be one of %s" % (INTERPRETATION,))
    if d.get("analysis_enabled") and d.get("interpretation") not in ("validated", "empirical"):
        errs.append("analysis_enabled needs a validated or empirical interpretation")
    if d.get("scope") not in SCOPES:
        errs.append("scope must be one of %s" % (SCOPES,))
    return errs


def signals_sha256(descriptors: List[Dict[str, Any]]) -> str:
    """Identity of a signal catalog: every field except free-text notes."""
    canon = [{k: v for k, v in d.items() if k != "notes"}
             for d in sorted(descriptors, key=lambda x: x["id"])]
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def typed_value(value: Any):
    """
    (int, real, text) with at most one filled, for typed storage.

    Integers outside the signed 64-bit range are kept exactly as text, never
    rounded to a float; None gives three Nones (missing, never 0).
    """
    if value is None:
        return None, None, None
    if isinstance(value, bool):
        return int(value), None, None
    if isinstance(value, int):
        return (value, None, None) if -_INT64_MAX - 1 <= value <= _INT64_MAX else (None, None, str(value))
    if isinstance(value, float):
        return None, value, None
    return None, None, str(value)


def counter_deltas(samples: List[Any]) -> Dict[str, Any]:
    """
    Within-epoch increments of one cumulative counter, from (time, raw value) pairs.

    A missing value (None) is skipped: the counter still covers the gap. A
    decrease is a discontinuity (reset, wrap or device change): a new epoch
    starts and no delta is computed across it, so a delta is never negative.
    """
    incs, disc, prev = [], 0, None
    for t, v in samples:
        if v is None:
            continue
        if prev is not None:
            if v >= prev[1]:
                incs.append((prev[0], t, v - prev[1]))
            else:
                disc += 1
        prev = (t, v)
    return {"increments": incs, "total": sum(d for _, _, d in incs),
            "intervals": len(incs), "discontinuities": disc, "complete": disc == 0}
