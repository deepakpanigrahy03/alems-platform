"""
core/readers/sampling_policy.py

A-LEMS sampling policy: automatic capability detection, explicit measurement
policy, recorded effective behaviour.

    effective_hz = min(requested_hz, probed_hz, schema.sampling_hz)

requested_hz
    config/sampling.yaml: required, versioned, one entry per source kind.
    This is the declared measurement policy, never a per machine setting.
probed_hz
    What this machine's source can deliver, measured once and cached in
    ~/.alems/sampling_probe.json. Capability only; it never chooses a rate.
schema.sampling_hz
    Safe maximum for the source kind (alems_sdk.measurement_schema).

No silent fallback (DC-3, MIC-1): a missing or invalid policy, or a source
without an entry, raises SamplingPolicyError before measurement starts.

Per run record: describe() returns requested, probed, schema bound, effective,
rate_limited, policy version and the policy file SHA256 for sampler_stats.
Python 3.9 compatible (PVC-1, PVC-2).
"""

import hashlib
import json
import logging
import os
import platform
import socket
import statistics
import time
from functools import lru_cache
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Probe length: long enough to see at least 10 updates of a 10 Hz sensor,
# short enough that the first run on a new machine waits under 2 s.
_PROBE_SECONDS = 1.5
# Re-probe monthly so firmware or OS updates that change the native rate are seen.
_PROBE_MAX_AGE_DAYS = 30
# Whitelists: anything else in the policy file is rejected, which keeps the
# file machine independent (no host names, no per machine overrides).
_ALLOWED_TOP = {"version", "sources", "tolerance"}
_ALLOWED_SOURCE_KEYS = {"requested_hz"}
_TOLERANCE_DEFAULTS = {"achieved_rate_rel": 0.20, "max_gap_intervals": 2.0}


class SamplingPolicyError(RuntimeError):
    """The sampling policy is missing, invalid, or does not cover a source."""


# ---------------------------------------------------------------- policy file
def _config_path() -> str:
    """
    Location of the policy file.

    Returns:
        ALEMS_SAMPLING_CONFIG if set (sandboxes and tests), else
        <repo>/config/sampling.yaml.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", ".."))
    return os.environ.get("ALEMS_SAMPLING_CONFIG", os.path.join(root, "config", "sampling.yaml"))


def _read_policy_bytes(path: str) -> bytes:
    """
    Read the raw policy file.

    Raw bytes are kept so the SHA256 recorded per run matches the file exactly.

    Raises:
        SamplingPolicyError: file missing or unreadable.
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        raise SamplingPolicyError(
            "sampling policy not found: %s. A-LEMS does not measure without a declared "
            "sampling policy (config/sampling.yaml)." % path)
    except OSError as e:
        raise SamplingPolicyError("sampling policy %s unreadable: %s" % (path, e))


def _parse_yaml(raw: bytes, path: str) -> dict:
    """
    Parse YAML into a dict.

    Raises:
        SamplingPolicyError: not valid YAML or not a mapping.
    """
    try:
        import yaml
        cfg = yaml.safe_load(raw) or {}
    except Exception as e:
        raise SamplingPolicyError("sampling policy %s is not valid YAML: %s" % (path, e))
    if not isinstance(cfg, dict):
        raise SamplingPolicyError("sampling policy %s must be a mapping" % path)
    return cfg


def _check_top(cfg: dict, path: str) -> None:
    """
    Top level keys: only version, sources, tolerance; version and sources required.

    Raises:
        SamplingPolicyError: unknown or missing keys.
    """
    unknown = set(cfg) - _ALLOWED_TOP
    if unknown:
        raise SamplingPolicyError("sampling policy %s: unknown keys %s" % (path, sorted(unknown)))
    if "version" not in cfg or not isinstance(cfg.get("sources"), dict) or not cfg["sources"]:
        raise SamplingPolicyError("sampling policy %s: needs 'version' and non empty 'sources'" % path)


def _check_source(name: str, entry, path: str) -> None:
    """
    One source entry: a source kind with requested_hz > 0 and nothing else.

    Rejecting extra keys is what keeps machine names out of the policy.

    Raises:
        SamplingPolicyError: wrong shape or rate.
    """
    if not isinstance(entry, dict) or set(entry) - _ALLOWED_SOURCE_KEYS:
        raise SamplingPolicyError(
            "sampling policy %s: source %s may only set requested_hz "
            "(entries are source kinds, never machines)" % (path, name))
    hz = entry.get("requested_hz")
    if not isinstance(hz, (int, float)) or hz <= 0:
        raise SamplingPolicyError("sampling policy %s: source %s needs requested_hz > 0" % (path, name))


def _tolerance(cfg: dict, path: str) -> Dict[str, float]:
    """
    Tolerances for invariant S2; unknown keys rejected, missing keys get documented defaults.

    Returns:
        {"achieved_rate_rel": float, "max_gap_intervals": float}
    """
    tol = cfg.get("tolerance") or {}
    unknown = set(tol) - set(_TOLERANCE_DEFAULTS)
    if unknown:
        raise SamplingPolicyError("sampling policy %s: unknown tolerance keys %s" % (path, sorted(unknown)))
    return {k: float(tol.get(k, v)) for k, v in _TOLERANCE_DEFAULTS.items()}


@lru_cache(maxsize=1)
def load_policy() -> dict:
    """
    Read and validate the policy once per process.

    Returns:
        The policy dict plus _path and _sha256 (recorded per run).

    Raises:
        SamplingPolicyError: any problem; nothing is assumed.
    """
    path = _config_path()
    raw = _read_policy_bytes(path)
    cfg = _parse_yaml(raw, path)
    _check_top(cfg, path)
    for name, entry in cfg["sources"].items():
        _check_source(name, entry, path)
    cfg["tolerance"] = _tolerance(cfg, path)
    cfg["_path"] = path
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    logger.info("sampling policy v%s loaded from %s (sha256 %s)", cfg["version"], path, cfg["_sha256"][:12])
    return cfg


def validate() -> dict:
    """
    Load the policy at engine start so a bad policy stops the experiment before its first run.

    Returns:
        The loaded policy.
    """
    return load_policy()


def requested_hz(source: str) -> float:
    """
    Declared rate for a source kind.

    Raises:
        SamplingPolicyError: the source kind has no entry (never defaulted).
    """
    cfg = load_policy()
    entry = cfg["sources"].get(source)
    if entry is None:
        raise SamplingPolicyError(
            "sampling policy %s has no entry for source %s; add it before measuring "
            "with this reader" % (cfg["_path"], source))
    return float(entry["requested_hz"])


def tolerance() -> Dict[str, float]:
    """Tolerances for invariant S2 from the policy file."""
    return load_policy()["tolerance"]


# ---------------------------------------------------------------- capability probe
def _cache_path() -> str:
    """Probe cache file; ALEMS_SAMPLING_PROBE_CACHE overrides for tests."""
    return os.environ.get("ALEMS_SAMPLING_PROBE_CACHE",
                          os.path.join(os.path.expanduser("~"), ".alems", "sampling_probe.json"))


def _machine_key(reader) -> str:
    """
    Capability cache key: host, OS build, CPU and reader class.

    Used only to cache what the hardware can do; never used to choose a rate.
    """
    return "|".join([socket.gethostname(), platform.platform(), platform.machine(),
                     platform.processor() or "", type(reader).__name__])


def _cache_load() -> dict:
    """Cached probe results; an unreadable cache means re-probe (logged at debug)."""
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except Exception as e:
        logger.debug("sampling_policy: probe cache not read (%s)", e)
        return {}


def _cache_store(key: str, entry: dict) -> None:
    """
    Write one probe result atomically (temp file then rename), so a crash
    mid write never leaves a corrupt cache.
    """
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = _cache_load()
        data[key] = entry
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning("sampling_policy: cannot write probe cache %s (%s)", path, e)


def _change_gaps(reader, seconds: float) -> list:
    """
    Read the source in a tight loop and return the intervals between value changes.

    0.5 ms polling resolves update periods down to about 1 ms (RAPL).
    """
    t_end = time.monotonic() + seconds
    prev = reader.read_energy()
    last = time.monotonic()
    gaps = []
    while time.monotonic() < t_end:
        time.sleep(0.0005)
        cur = reader.read_energy()
        if not cur or cur == prev:
            continue
        now = time.monotonic()
        gaps.append(now - last)
        last, prev = now, cur
    return gaps


def _probe_counter(reader, seconds: float = _PROBE_SECONDS) -> Optional[float]:
    """
    Native update rate of a counter or rail reader.

    Returns:
        1 / median interval between changes (first, partial interval dropped),
        or None if fewer than 3 changes were seen.
    """
    gaps = _change_gaps(reader, seconds)
    if len(gaps) < 3:
        return None
    period = statistics.median(gaps[1:])
    return 1.0 / period if period > 0 else None


def _cached_probe(key: str) -> Optional[dict]:
    """Return a fresh cached probe entry, or None if absent, stale, or re-probe forced."""
    if os.environ.get("ALEMS_SAMPLING_REPROBE"):
        return None
    hit = _cache_load().get(key)
    if not hit or time.time() - hit.get("probed_at", 0) >= _PROBE_MAX_AGE_DAYS * 86400:
        return None
    return hit


def probe_hz(reader, force: bool = False) -> Optional[float]:
    """
    Capability of this machine's source, cached per machine and reader.

    A reader may provide probe_native_hz() (IOKit does); otherwise the counter
    change probe is used. A failed probe is logged and returns None, so the
    schema bound still applies.

    Returns:
        Probed rate in Hz, or None if unknown.
    """
    key = _machine_key(reader)
    hit = None if force else _cached_probe(key)
    if hit is not None:
        return hit.get("probed_hz")
    custom = getattr(reader, "probe_native_hz", None)
    method = "reader" if callable(custom) else "counter_change"
    try:
        hz = custom() if callable(custom) else _probe_counter(reader)
    except Exception as e:
        logger.warning("sampling_policy: probe failed for %s (%s); schema bound applies",
                       type(reader).__name__, e)
        return None
    _cache_store(key, {"probed_hz": hz, "method": method, "probed_at": time.time()})
    logger.info("sampling_policy: probed %s = %s Hz (%s)", type(reader).__name__,
                "%.1f" % hz if hz else "unknown", method)
    return hz


# ---------------------------------------------------------------- policy decision
def effective_hz(source: str, hardware_max_hz: Optional[float],
                 probed: Optional[float] = None) -> float:
    """
    min(requested, probed, schema bound); each limit applied is logged.

    Args:
        source: schema source kind (RAPL, SPBM, IOKIT, GPU_COLLECTOR, ...).
        hardware_max_hz: schema.sampling_hz.
        probed: probe_hz() result or None.

    Returns:
        Rate in Hz the sampler must use.
    """
    req = requested_hz(source)
    eff = req
    for bound, label in ((probed, "probed capability"), (hardware_max_hz, "schema bound")):
        if not bound or eff <= bound:
            continue
        logger.warning("sampling_policy: %s policy %.1f Hz limited by %s %.1f Hz", source, req, label, bound)
        eff = float(bound)
    return eff


def effective_for_schema(schema, reader=None) -> float:
    """Effective rate for a MeasurementSchema; probes the reader when given."""
    probed = probe_hz(reader) if reader is not None else None
    return effective_hz(schema.source, getattr(schema, "sampling_hz", None), probed)


def describe(schema, reader=None) -> dict:
    """
    Everything a run records about its sampling rate (table sampler_stats).

    Returns:
        requested_hz, probed_hz, schema_max_hz, effective_hz, rate_limited
        (1 when effective is below the policy), policy_version, policy_sha256.
    """
    cfg = load_policy()
    probed = probe_hz(reader) if reader is not None else None
    bound = getattr(schema, "sampling_hz", None)
    req = requested_hz(schema.source)
    eff = effective_hz(schema.source, bound, probed)
    return {"requested_hz": req, "probed_hz": probed, "schema_max_hz": bound,
            "effective_hz": eff, "rate_limited": int(eff < req),
            "policy_version": str(cfg["version"]), "policy_sha256": cfg["_sha256"]}
