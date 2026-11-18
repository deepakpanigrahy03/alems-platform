"""
================================================================================
THERMAL EXPOSURE: MACHINE PROFILE LOADER
================================================================================

PURPOSE:
    Load a machine profile (config/machine_profiles/<profile>.yaml), check it
    against the hardware catalog this machine discovered (power_rails,
    energy_domains, thermal_zones), and write te_channel and te_sensor rows.

DESIGN:
    The profile holds decisions (which channels and sensors, their role,
    boundary, the main pair). Facts (units, existence, a zone's canonical
    role) come from the catalog that detect_hardware / hw_config filled.
    Nothing is machine specific in this code: a profile that names a rail,
    domain or zone this machine did not discover is rejected, so a profile
    for one platform can never break another.

RULES:
    * Writes go through the writer handle the extension manager passes to
      on_activate(); this module never opens its own connection (WCC-2).
    * Imports only the standard library and yaml (CH39-2: no core imports).
    * Every problem in a profile is collected and reported together, and the
      profile is rejected as a whole: no partial register is written.
================================================================================
"""

import hashlib
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# Allowed values, mirrored from the CHECK constraints in e001 (kept in one place here)
CHANNEL_SOURCES = ("power_rail_samples", "energy_sample_domains", "gpu_samples")
SENSOR_SOURCES = ("thermal_samples_v2", "gpu_samples")
CHANNEL_ROLES = ("component", "aggregate", "overlapping")
# Unit and measurement kind each source can deliver (facts of the store layout)
SOURCE_UNIT = {"power_rail_samples": "mW", "energy_sample_domains": "uJ", "gpu_samples": "uJ"}
SOURCE_KIND = {"power_rail_samples": ("power_reading",),
               "energy_sample_domains": ("interval_energy", "energy_counter"),
               "gpu_samples": ("energy_counter",)}
SYSFS_THERMAL = "/sys/class/thermal"       # Linux thermal zone root (kernel ABI)


class ProfileError(ValueError):
    """Raised when a machine profile is rejected; message lists every problem."""


# ------------------------------------------------------------------ file
def load_profile_file(path: str) -> Tuple[Dict[str, Any], str]:
    """
    Read a profile YAML and its SHA-256.

    Args:
        path: Path to the profile file.

    Returns:
        (profile dict, sha256 hex of the file bytes).
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ProfileError("%s: profile must be a mapping" % path)
    return data, hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ writer handle
def resolve_execute(db: Any) -> Callable:
    """
    Find the execute method of the writer handle given to on_activate().

    Accepts the writer itself or a manager wrapping it; never opens a
    connection of its own.

    Returns:
        A callable execute(sql, params).
    """
    for path in (("execute",), ("conn", "execute"), ("db", "execute"), ("db", "conn", "execute")):
        obj = db
        for attr in path:
            obj = getattr(obj, attr, None)
            if obj is None:
                break
        if callable(obj):
            return obj
    raise ProfileError("writer handle %s has no execute method" % type(db).__name__)


# ------------------------------------------------------------------ catalog (facts)
def _rows(execute: Callable, sql: str, params: tuple = ()) -> List[tuple]:
    """
    Run a read query and return a list of tuples.

    The writer returns a list of dicts (column order kept); a sqlite3
    connection returns a cursor. Both are accepted.
    """
    res = execute(sql, params)
    if hasattr(res, "fetchall"):
        return [tuple(r) for r in res.fetchall()]
    return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in (res or [])]


def _table_exists(execute: Callable, name: str) -> bool:
    """True if a table exists in the store (power_rails exists only on GB10 class)."""
    return bool(_rows(execute, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _gpu_reader_available() -> bool:
    """True if an NVIDIA GPU reader can run here (NVML library or nvidia-smi)."""
    try:
        import pynvml                    # optional dependency; absence is not an error
        pynvml.nvmlInit()
        pynvml.nvmlShutdown()
        return True
    except Exception as exc:             # library missing or no driver
        logger.debug("profile loader: NVML unavailable (%s); trying nvidia-smi", exc)
    return shutil.which("nvidia-smi") is not None


def read_catalog(execute: Callable, machine_id: str) -> Dict[str, Any]:
    """
    Collect the discovered hardware facts the profile is checked against.

    Args:
        execute:    Writer execute callable (read queries only here).
        machine_id: Key used by thermal_zones for this machine (lookup only;
                    never stored in te_ tables).

    Returns:
        Dict with rails, domains, zones and gpu availability.
    """
    rails, domains, zones = {}, {}, {}
    if _table_exists(execute, "power_rails"):
        for rail_id, name in _rows(execute, "SELECT rail_id, rail_name FROM power_rails"):
            rails[name] = rail_id
    for dom_id, name, unit, cum in _rows(
            execute, "SELECT domain_id, name, unit, is_cumulative FROM energy_domains"):
        domains[name] = (dom_id, (unit or "").lower(), cum)
    for ztype, zidx, role in _rows(
            execute, "SELECT zone_type, zone_index, canonical_role FROM thermal_zones "
            "WHERE machine_id = ? AND active = 1", (machine_id,)):
        zones[(ztype, zidx)] = role
    return {"rails": rails, "domains": domains, "zones": zones,
            "gpu": _gpu_reader_available()}


# ------------------------------------------------------------------ validation
def _check_names(items: List[Dict], kind: str, errors: List[str]) -> List[str]:
    """Collect names; report missing and duplicate names."""
    names = []
    for i, it in enumerate(items):
        name = it.get("name")
        if not name:
            errors.append("%s #%d has no name" % (kind, i))
        elif name in names:
            errors.append("duplicate %s name: %s" % (kind, name))
        names.append(name)
    return names


def _channel_key(ch: Dict, cat: Dict, errors: List[str]) -> Optional[str]:
    """Resolve a channel's source_key to the stored key; report unknown keys."""
    src, key = ch.get("source_table"), ch.get("source_key")
    if src == "power_rail_samples":
        if not cat["rails"]:
            errors.append("%s: power_rail_samples has no usable reader here" % ch["name"])
        elif key not in cat["rails"]:
            errors.append("%s: unknown rail '%s'" % (ch["name"], key))
        else:
            return str(cat["rails"][key])
    elif src == "energy_sample_domains":
        if key not in cat["domains"]:
            errors.append("%s: unknown energy domain '%s'" % (ch["name"], key))
        else:
            return str(cat["domains"][key][0])
    elif src == "gpu_samples":
        if not cat["gpu"]:
            errors.append("%s: gpu_samples has no usable reader here" % ch["name"])
        else:
            return str(key)
    else:
        errors.append("%s: unknown source table '%s'" % (ch.get("name"), src))
    return None


def _check_channel(ch: Dict, names: List[str], errors: List[str]) -> None:
    """Unit, kind, role and parent rules for one channel."""
    src, name = ch.get("source_table"), ch.get("name")
    if src in SOURCE_UNIT and ch.get("unit") != SOURCE_UNIT[src]:
        errors.append("%s: invalid unit '%s' for %s (need %s)" % (name, ch.get("unit"), src, SOURCE_UNIT[src]))
    if src in SOURCE_KIND and ch.get("measurement_kind") not in SOURCE_KIND[src]:
        # also rejects gpu_samples.power_mw: GPU power must come from rails or domains
        errors.append("%s: measurement_kind '%s' not delivered by %s" % (name, ch.get("measurement_kind"), src))
    if ch.get("channel_role") not in CHANNEL_ROLES:
        errors.append("%s: invalid channel_role '%s'" % (name, ch.get("channel_role")))
    if ch.get("parent") and ch["parent"] not in names:
        errors.append("%s: parent '%s' not listed" % (name, ch["parent"]))
    if not ch.get("meaning"):
        errors.append("%s: meaning is required" % name)


def _sensor_role(s: Dict, cat: Dict, errors: List[str]) -> Optional[str]:
    """Canonical role of a sensor from the catalog; report unknown or unreadable sensors."""
    src, name = s.get("source_table"), s.get("name")
    if src == "thermal_samples_v2":
        key = (s.get("zone_type"), s.get("zone_index"))
        if key not in cat["zones"]:
            errors.append("%s: zone %s/%s not discovered on this machine" % (name, key[0], key[1]))
            return None
        if not os.access(os.path.join(SYSFS_THERMAL, "thermal_zone%s" % key[1], "temp"), os.R_OK):
            errors.append("%s: zone %s has no readable temp file" % (name, key[1]))
        return cat["zones"][key]
    if src == "gpu_samples":
        if not cat["gpu"]:
            errors.append("%s: gpu_samples has no usable reader here" % name)
        return s.get("canonical_role") or "GPU"
    errors.append("%s: unknown or unsupported source table '%s'" % (name, src))
    return None


def _check_sensor(s: Dict, errors: List[str]) -> None:
    """Unit, resolution and validity range rules for one sensor."""
    name = s.get("name")
    if s.get("unit", "C") != "C":
        errors.append("%s: invalid unit '%s' (need C)" % (name, s.get("unit")))
    res = s.get("nominal_resolution_c")
    if not isinstance(res, (int, float)) or res <= 0:
        errors.append("%s: nominal_resolution_c must be > 0" % name)
    lo, hi = s.get("valid_min_c"), s.get("valid_max_c")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or lo >= hi:
        errors.append("%s: need valid_min_c < valid_max_c" % name)
    if s.get("expected_update_interval_ms") is not None and not s.get("expected_update_interval_source"):
        errors.append("%s: an expected update interval needs its source" % name)


def build_rows(profile: Dict, sha: str, cat: Dict) -> Tuple[List[tuple], List[tuple]]:
    """
    Validate a profile against the catalog and build te_channel / te_sensor rows.

    Raises:
        ProfileError listing every problem; nothing is returned on failure.
    """
    errors: List[str] = []
    pid = profile.get("profile_id")
    if not pid:
        errors.append("profile_id is required")
    chans, sens = profile.get("channels") or [], profile.get("sensors") or []
    cnames = _check_names(chans, "channel", errors)
    snames = _check_names(sens, "sensor", errors)
    if profile.get("main_channel") not in cnames:
        errors.append("main_channel '%s' not listed" % profile.get("main_channel"))
    if profile.get("main_sensor") not in snames:
        errors.append("main_sensor '%s' not listed" % profile.get("main_sensor"))
    now = datetime.now(timezone.utc).isoformat()
    crow = [_channel_row(c, pid, sha, cat, cnames, profile, now, errors) for c in chans]
    srow = [_sensor_row(s, pid, sha, cat, profile, now, errors) for s in sens]
    if errors:
        raise ProfileError("profile %s rejected:\n  - %s" % (pid, "\n  - ".join(errors)))
    return crow, srow


def _channel_row(c, pid, sha, cat, names, profile, now, errors) -> tuple:
    """One te_channel row (attribution_eligible: boundary known and not overlapping)."""
    _check_channel(c, names, errors)
    key = _channel_key(c, cat, errors)
    eligible = int(c.get("boundary") is not None and c.get("channel_role") != "overlapping")
    return (pid, sha, c.get("name"), c.get("source_table"), key, c.get("unit"),
            c.get("meaning"), c.get("measurement_kind"), c.get("channel_role"),
            c.get("parent"), c.get("boundary"), eligible,
            int(c.get("name") == profile.get("main_channel")), now)


def _sensor_row(s, pid, sha, cat, profile, now, errors) -> tuple:
    """One te_sensor row; canonical role taken from the catalog for zones."""
    _check_sensor(s, errors)
    role = _sensor_role(s, cat, errors)
    return (pid, sha, s.get("name"), s.get("source_table"), s.get("zone_type"),
            s.get("zone_index"), s.get("gpu_index"), role, "C",
            s.get("nominal_resolution_c"), s.get("valid_min_c"), s.get("valid_max_c"),
            s.get("expected_update_interval_ms"), s.get("expected_update_interval_source"),
            int(s.get("name") == profile.get("main_sensor")), now)


# ------------------------------------------------------------------ write
_INSERT_CHANNEL = (
    "INSERT INTO te_channel (machine_profile, profile_sha256, channel_name, source_table,"
    " source_key, unit, meaning, measurement_kind, channel_role, parent_channel, boundary,"
    " attribution_eligible, is_main, loaded_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
_INSERT_SENSOR = (
    "INSERT INTO te_sensor (machine_profile, profile_sha256, sensor_name, source_table,"
    " zone_type, zone_index, gpu_index, canonical_role, unit, nominal_resolution_c,"
    " valid_min_c, valid_max_c, expected_update_interval_ms, expected_update_interval_source,"
    " is_main, loaded_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def write_rows(execute: Callable, profile_id: str, crow: List[tuple], srow: List[tuple]) -> None:
    """
    Replace this profile's register rows (idempotent on re-activation).

    The caller's writer owns the transaction and commit.
    """
    execute("DELETE FROM te_channel WHERE machine_profile = ?", (profile_id,))
    execute("DELETE FROM te_sensor WHERE machine_profile = ?", (profile_id,))
    for r in crow:
        execute(_INSERT_CHANNEL, r)
    for r in srow:
        execute(_INSERT_SENSOR, r)
    logger.info("thermal_exposure: profile %s loaded (%d channels, %d sensors)",
                profile_id, len(crow), len(srow))


def load_into_store(db: Any, profile_path: str, machine_id: str) -> Tuple[int, int]:
    """
    Load, validate and write one profile through the given writer handle.

    Returns:
        (channel rows, sensor rows) written.
    """
    execute = resolve_execute(db)
    profile, sha = load_profile_file(profile_path)
    crow, srow = build_rows(profile, sha, read_catalog(execute, machine_id))
    write_rows(execute, profile["profile_id"], crow, srow)
    _commit(db)
    return len(crow), len(srow)


def _commit(db: Any) -> None:
    """Commit through the same handle (writer or its connection), if it exposes commit."""
    for target in (db, getattr(db, "conn", None), getattr(getattr(db, "db", None), "conn", None)):
        if target is not None and callable(getattr(target, "commit", None)):
            target.commit()
            return
    logger.debug("thermal_exposure: writer handle has no commit(); caller commits")
