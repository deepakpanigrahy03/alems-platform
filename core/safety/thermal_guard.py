"""
core/safety/thermal_guard.py: hardware safety guard for every run path.

The guard decides whether new work may start and stops work when the hardware
gets too close to its limit. It is independent of extensions and engines.

Headroom: margin in C between a component's temperature and a limit the
component itself reports (for NVIDIA GPUs the slowdown margin, "T.Limit").
The guard takes the smallest headroom over every required source, so the
hottest part decides. A source that stops answering never makes the minimum
look better: its last value is kept for at most stale_after_s, after which the
guard is blind, which blocks admission and stops active work.

Rules (all numbers from config safety.thermal; no defaults in code):
  admit()   before every dispatch: minimum rest since the end of
            the previous workload (a requested backoff can lengthen it, never
            shorten it), then headroom >= start level continuously for
            start_stable_s on fresh readings, within max_wait_s.
            start level = max(start_floor_headroom_c, idle_headroom - k) when
            k (start_idle_margin_c) is set, else the floor; with k set and no
            valid idle reference the gate refuses (fail closed).
  tick()    every interval_s for the whole episode (watchdog thread): one
            sample row; headroom below stop_headroom_c during a run is
            recorded (stop_level_crossed), never acted on: the run completes
            and the next run waits at the gate. In-run protection is the
            hardware's own slowdown and shutdown.
The guard never aborts a run and never latches; it only waits. Gate waits are
unbounded: every max_wait_s a warning is recorded (idle_headroom_insufficient
while nothing has run yet, gate_timeout afterwards) and the wait continues.
A silent required source blocks admission until it answers again.
Every event is appended to the host log first (it survives a crash), then
stored; log_written records whether the append worked. A store failure never
blocks a safety action.
"""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

QUALITIES = ("fresh", "stale", "unavailable", "failed", "invalid")
# Severity order used to report the worst quality of a tick; later is worse.
_QUALITY_ORDER = ("fresh", "stale", "invalid", "failed", "unavailable")
_NUM, _NUM_OR_NULL, _BOOL, _TEXT = "number", "number_or_null", "bool", "text"
# Every key is required: a safety limit must never come from a silent default.
CONFIG_KEYS = {
    "policy_version": _TEXT,
    "enabled": _BOOL,
    "interval_s": _NUM,
    "start_floor_headroom_c": _NUM,
    "start_idle_margin_c": _NUM_OR_NULL,
    "start_stable_s": _NUM,
    "max_wait_s": _NUM,
    "stop_headroom_c": _NUM,
    "min_rest_s": _NUM,
    "stale_after_s": _NUM,
    "allow_without_source": _BOOL,
    "rise_warn_c_per_s": _NUM_OR_NULL,
}


class GuardConfigError(ValueError):
    """The safety.thermal block is missing a key or has a wrong value."""


class GuardRefused(RuntimeError):
    """Work may not start; code is a stable reason code."""

    def __init__(self, code, detail=""):
        # type: (str, str) -> None
        """Keep the code separate so callers branch on it, never on message text."""
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code


def _check_value(key, kind, value, errors):
    # type: (str, str, Any, List[str]) -> Any
    """Validate one config value; append a readable error and return the normalised value."""
    if kind == _BOOL:
        if not isinstance(value, bool):
            errors.append("%s must be true or false" % key)
        return value
    if kind == _TEXT:
        return str(value)
    if value is None and kind == _NUM_OR_NULL:
        return None                     # null means "not decided yet", never zero
    # bool is a subclass of int in Python; reject it so `true` never becomes 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append("%s must be a number" % key)
        return value
    if value < 0:
        errors.append("%s must not be negative" % key)
    return float(value)


def load_config(block):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """
    Validate safety.thermal; every key required, no defaults.

    Raises:
        GuardConfigError listing every problem at once.
    """
    if not isinstance(block, dict):
        raise GuardConfigError("safety.thermal block missing")
    errors = []  # type: List[str]
    out = {}
    for key, kind in CONFIG_KEYS.items():
        if key not in block:
            errors.append("missing key safety.thermal.%s" % key)
            continue
        out[key] = _check_value(key, kind, block[key], errors)
    if not errors and out["interval_s"] <= 0:
        errors.append("interval_s must be above 0")
    # A stop level at or above the start level would trip right after admission.
    if not errors and out["stop_headroom_c"] >= out["start_floor_headroom_c"]:
        errors.append("stop_headroom_c must be below start_floor_headroom_c")
    if errors:
        raise GuardConfigError("; ".join(errors))
    return out


def config_sha256(cfg):
    # type: (Dict[str, Any]) -> str
    """Fingerprint of the limits in force, stored with every episode."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def reading(source, component, limit_type, headroom_c, quality, observed_mono_ns, raw=None):
    # type: (str, str, str, Optional[float], str, int, Optional[Dict]) -> Dict[str, Any]
    """One headroom observation; a missing value is None with a quality word, never 0."""
    if quality not in QUALITIES:
        raise ValueError("quality %r" % quality)
    if quality == "fresh" and headroom_c is None:
        quality = "failed"              # a fresh reading must carry a value
    return {"source": source, "component": component, "limit_type": limit_type,
            "headroom_c": None if headroom_c is None else float(headroom_c),
            "quality": quality, "observed_mono_ns": observed_mono_ns, "raw": raw or {}}


def _worse(a, b):
    # type: (str, str) -> str
    """The worse of two quality words."""
    return a if _QUALITY_ORDER.index(a) >= _QUALITY_ORDER.index(b) else b


class NvmlMarginSource(object):
    """GPU slowdown margins from core.readers.nvml_signals.NvmlSignals (no second NVML reader)."""

    name = "nvml"

    def __init__(self, signals):
        # type: (Any) -> None
        """Wrap an NvmlSignals instance; it already degrades to no signals without a driver."""
        self._s = signals

    def read(self, now_mono_ns):
        # type: (int) -> List[Dict[str, Any]]
        """One reading per GPU that reports a margin; an unreadable margin is failed, never 0."""
        if not getattr(self._s, "ok", False):
            return []
        try:
            sig = self._s.read_signals() or {}
        except Exception as exc:
            logger.warning("safety guard: NVML read failed: %s", exc)
            return []
        keys = [k for k in sig if k.endswith(":temp_margin_c")]
        return [self._one(sig, k, now_mono_ns) for k in keys]

    @staticmethod
    def _one(sig, key, now):
        # type: (Dict[str, Any], str, int) -> Dict[str, Any]
        """Margin of one GPU with its raw throttle bitmask and counters kept as evidence."""
        gpu = key.split(":")[1]
        val = sig.get(key) or {}
        ok = val.get("value") is not None and val.get("status") == "VALID"
        raw = {}
        reasons = sig.get("nvml:%s:clocks_event_reasons" % gpu)
        if reasons is not None:
            raw["clocks_event_reasons"] = reasons.get("value")
        prefix = "nvml:%s:reason_ns:" % gpu
        # Counters are cumulative and overlap; kept raw, deltas belong to analysis.
        counters = {k[len(prefix):]: (v or {}).get("value") for k, v in sig.items() if k.startswith(prefix)}
        if counters:
            raw["reason_ns"] = counters
        return reading("nvml", gpu, "slowdown_margin", val.get("value") if ok else None,
                       "fresh" if ok else "failed", now, raw)


class ZoneTripSource(object):
    """
    Thermal zones against their own critical trip point (Linux sysfs, through
    core.thermal.thermal_descriptors). A zone contributes a headroom only when it
    reports a readable critical trip above its current temperature; otherwise it is
    recorded as invalid and never decides the minimum.
    """

    name = "thermal_zone"

    def __init__(self, list_zones, trip_points, read_zone_c):
        # type: (Callable[[], List[Dict]], Callable[[str], List[Dict]], Callable[[str], Optional[float]]) -> None
        """Helpers are injected (the descriptor functions in production, fakes in tests)."""
        self._list, self._trips, self._read = list_zones, trip_points, read_zone_c

    def read(self, now_mono_ns):
        # type: (int) -> List[Dict[str, Any]]
        """One reading per zone; zones without a trustworthy critical trip are invalid."""
        try:
            zones = self._list() or []
        except Exception as exc:
            logger.warning("safety guard: thermal zone listing failed: %s", exc)
            return []
        return [self._one(z, now_mono_ns) for z in zones]

    def _one(self, zone, now):
        # type: (Dict[str, Any], int) -> Dict[str, Any]
        """Headroom = critical trip minus temperature, both read now from the same zone."""
        comp = "%s:%s" % (zone.get("index"), zone.get("type"))
        temp = _safe(lambda: self._read(zone["dir"]))
        crit = _critical_trip(_safe(lambda: self._trips(zone["dir"])) or [])
        raw = {"temp_c": temp, "critical_c": crit}
        if temp is None:
            return reading("thermal_zone", comp, "critical_trip", None, "failed", now, raw)
        if crit is None or crit <= temp:
            # No trip, an unreadable trip, or a trip at or below the current temperature
            # (placeholder zones) cannot be trusted as a limit.
            return reading("thermal_zone", comp, "critical_trip", None, "invalid", now, raw)
        return reading("thermal_zone", comp, "critical_trip", crit - temp, "fresh", now, raw)


def _critical_trip(limits):
    # type: (List[Dict[str, Any]]) -> Optional[float]
    """Lowest readable trip point of kind critical; None when the zone reports none."""
    temps = [l.get("temp_c") for l in limits if l.get("type") == "critical" and l.get("temp_c") is not None]
    return min(temps) if temps else None


def _safe(fn):
    # type: (Callable[[], Any]) -> Any
    """Call a sysfs helper; a read error is None (logged), never an exception or 0."""
    try:
        return fn()
    except Exception as exc:
        logger.warning("safety guard: thermal zone read failed: %s", exc)
        return None


class ThermalGuard(object):
    """One guard episode; see the module docstring for the rules."""

    def __init__(self, config, sources, log, store=None, entry_path="other",
                 mono_ns=time.monotonic_ns, utc_ns=time.time_ns, sleep=time.sleep,
                 seq_session_id=None, te_session_id=None):
        # type: (Dict[str, Any], List[Any], Callable[[Dict], bool], Any, str, Callable[[], int], Callable[[], int], Callable[[float], None], Optional[int], Optional[int]) -> None
        """Validate the config first, so an invalid limit never produces a running guard."""
        self.cfg = load_config(config)
        self._sources = list(sources)
        self._log = log
        self._store = store
        # Clocks and sleep are injected so tests can replay traces without real time.
        self._mono, self._utc, self._sleep = mono_ns, utc_ns, sleep
        self._lock = threading.RLock()  # reentrant: events emitted inside tick() take the lock again
        self.episode = self._new_episode(entry_path, seq_session_id, te_session_id)
        self._event_seq = 0
        self._sample_seq = 0
        self._last = {}            # (source, component) -> last fresh reading
        self._required = set()     # components seen fresh: they must stay observable
        self._state = {"min": None, "quality": "unavailable", "blind": True, "reading": None}
        self._active = 0
        self._last_end_mono = None
        self._admitted = 0
        self._idle_ref = None
        self._blind_reported = False
        self._crossing_reported = False
        self._rise_reported = False
        self._watchdog = None      # type: Optional[threading.Thread]
        self._stop_watchdog = threading.Event()
        self.unstored_events = []  # type: List[Dict[str, Any]]
        self.unstored_samples = 0
        self.no_source = False

    def _new_episode(self, entry_path, seq_session_id, te_session_id):
        # type: (str, Optional[int], Optional[int]) -> Dict[str, Any]
        """Episode record; the config and its hash are stored so limits in force are provable."""
        return {
            "episode_id": str(uuid.uuid4()), "entry_path": entry_path, "pid": os.getpid(),
            "started_utc_ns": None, "started_mono_ns": None, "ended_utc_ns": None, "ended_mono_ns": None,
            "seq_session_id": seq_session_id, "te_session_id": te_session_id,
            "policy_version": self.cfg["policy_version"], "config_json": json.dumps(self.cfg, sort_keys=True),
            "config_sha256": config_sha256(self.cfg), "status": "running", "latched": 0,
            "latch_reason": None, "end_reason": None,
        }

    # ------------------------------------------------------------ lifecycle
    def open(self):
        # type: () -> None
        """Start the episode; refuse when no headroom source exists (unless allowed)."""
        now_m = self._mono()
        self.episode.update(started_utc_ns=self._utc(), started_mono_ns=now_m)
        # The episode row is queued before any event, so event rows always find their parent.
        self._call_store("store_episode", dict(self.episode))
        self._emit("episode_start", detail={"entry_path": self.episode["entry_path"],
                                            "config_sha256": self.episode["config_sha256"]})
        for r in self._read_all(now_m):
            if r["quality"] == "fresh":
                self._required.add((r["source"], r["component"]))
                self._last[(r["source"], r["component"])] = r
        if not self._required:
            self._no_source()
        self.tick()

    def _no_source(self):
        # type: () -> None
        """No headroom source: refuse unless the config explicitly allows running unguarded."""
        self._emit("no_source", quality="unavailable",
                   detail={"allow_without_source": self.cfg["allow_without_source"]})
        if not self.cfg["allow_without_source"]:
            self.close("no_source")
            raise GuardRefused("no_source", "no headroom source on this machine")
        self.no_source = True

    def start_watchdog(self):
        # type: () -> None
        """Background thread: tick every interval_s until close()."""
        self._watchdog = threading.Thread(target=self._watch, name="safety-guard-watchdog", daemon=True)
        self._watchdog.start()

    def _watch(self):
        # type: () -> None
        """Watchdog loop; a failing tick is logged and monitoring continues."""
        while not self._stop_watchdog.wait(self.cfg["interval_s"]):
            try:
                self.tick()
            except Exception as exc:
                logger.error("safety guard: tick failed: %s", exc)

    def close(self, reason="completed"):
        # type: (str) -> None
        """End the episode and record why."""
        self._stop_watchdog.set()
        if self._watchdog is not None and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=5)
        with self._lock:
            if self.episode["ended_mono_ns"] is not None:
                return                  # closing twice (atexit plus explicit close) is harmless
            self.episode.update(ended_utc_ns=self._utc(), ended_mono_ns=self._mono(), end_reason=reason,
                                status="ended")
            self._emit("episode_end", detail={"reason": reason})
            self._call_store("update_episode", dict(self.episode))

    # ------------------------------------------------------------ hooks for the harness
    def set_idle_reference(self, headroom_c, binding):
        # type: (float, Dict[str, Any]) -> None
        """Loaded-idle headroom measured for the declared server state (binding says which)."""
        with self._lock:
            self._idle_ref = {"headroom_c": float(headroom_c), "binding": dict(binding), "valid": True,
                              "set_mono_ns": self._mono()}
            self._emit("idle_reference_set", value_c=float(headroom_c), detail={"binding": binding})

    def invalidate_idle_reference(self, reason):
        # type: (str) -> None
        """Driver or server restart, model reload: the reference no longer describes this machine state."""
        with self._lock:
            if self._idle_ref is not None:
                self._idle_ref["valid"] = False
            self._emit("idle_reference_invalid", detail={"reason": reason})

    def workload_started(self, links=None):
        # type: (Optional[Dict[str, Any]]) -> None
        """A measured run began (the guard only watches it; it never interrupts a run)."""
        with self._lock:
            self._active += 1

    def workload_ended(self, links=None):
        # type: (Optional[Dict[str, Any]]) -> None
        """A measured run ended; minimum rest is measured from this moment."""
        with self._lock:
            self._active = max(0, self._active - 1)
            self._last_end_mono = self._mono()

    def active_count(self):
        # type: () -> int
        """Workloads started and not yet ended."""
        return self._active

    # ------------------------------------------------------------ admission
    def start_level(self):
        # type: () -> float
        """Headroom needed to admit work: max(floor, idle - k), or the floor while k is unset."""
        floor, k = self.cfg["start_floor_headroom_c"], self.cfg["start_idle_margin_c"]
        if k is None:
            return floor
        ref = self._idle_ref
        if ref is None or not ref["valid"]:
            # Fail closed: a stale or missing reference must never weaken the gate.
            raise GuardRefused("idle_reference_invalid", "k is set but no valid idle reference")
        return max(floor, ref["headroom_c"] - k)

    def admit(self, requested_rest_s=0.0, links=None):
        # type: (float, Optional[Dict[str, Any]]) -> Dict[str, Any]
        """Block until work may start (unbounded wait); raise GuardRefused only for an invalid idle reference."""
        links = links or {}
        self._wait_rest(requested_rest_s, links)
        if self.no_source:
            self._admitted += 1
            self._emit("gate_open", links=links, quality="unavailable", detail={"no_source": True})
            return {"start_level_c": None, "headroom_c": None}
        try:
            level = self.start_level()
        except GuardRefused:
            self._emit("idle_reference_invalid", links=links, detail={"at": "admission"})
            raise
        self._emit("gate_wait", threshold_c=level, links=links)
        return self._wait_gate(level, links)

    def _wait_rest(self, requested_rest_s, links):
        # type: (float, Dict[str, Any]) -> None
        """Minimum rest from the end of the previous workload; a backoff can only lengthen it."""
        rest_s = max(self.cfg["min_rest_s"], float(requested_rest_s or 0.0))
        if self._last_end_mono is None:
            return                      # nothing ran yet in this episode
        until = self._last_end_mono + int(rest_s * 1e9)
        if self._mono() < until:
            self._emit("rest_wait", links=links, detail={"rest_s": rest_s, "requested_rest_s": requested_rest_s})
        while self._mono() < until:
            self._sleep(min(self.cfg["interval_s"], (until - self._mono()) / 1e9))

    def _wait_gate(self, level, links):
        # type: (float, Dict[str, Any]) -> Dict[str, Any]
        """Wait for start_stable_s of fresh headroom at or above level; warn every max_wait_s, never give up."""
        t0 = self._mono()                # start of the current warning period
        stable_ns = int(self.cfg["start_stable_s"] * 1e9)
        stable_since = None
        while True:
            now = self._mono()
            stable_since = self._stable_since(level, stable_since, now)
            admitted = None
            if stable_since is not None and now - stable_since >= stable_ns:
                admitted = self._open_gate(level, links)
            if admitted is not None:
                return admitted
            if now - t0 >= int(self.cfg["max_wait_s"] * 1e9):
                self._gate_timeout(level, links, (now - t0) / 1e9)
                t0 = now                 # next warning one full period later (rate limited)
            self._sleep(self.cfg["interval_s"])

    def _stable_since(self, level, since, now):
        # type: (float, Optional[int], int) -> Optional[int]
        """Start of the current run of good readings; None when the latest reading is not good."""
        st = self._state
        good = st["min"] is not None and not st["blind"] and st["min"] >= level
        if not good:
            return None                 # any dip or blind tick restarts the stable period
        return now if since is None else since

    def _open_gate(self, level, links):
        # type: (float, Dict[str, Any]) -> Optional[Dict[str, Any]]
        """Admit, rechecking the latest reading immediately before admission; None if it changed."""
        st = self._state                # the watchdog may have ticked since the stability check
        if st["blind"] or st["min"] is None or st["min"] < level:
            return None
        self._admitted += 1
        self._emit("gate_open", value_c=st["min"], threshold_c=level, links=links,
                   reading=st["reading"], quality="fresh")
        return {"start_level_c": level, "headroom_c": st["min"]}

    def _gate_timeout(self, level, links, waited_s):
        # type: (float, Dict[str, Any], float) -> None
        """Availability warning for a long wait; the wait continues and nothing is ended."""
        st = self._state
        code = "idle_headroom_insufficient" if self._admitted == 0 else "gate_timeout"
        self._emit(code, value_c=st["min"], threshold_c=level, links=links, reading=st["reading"],
                   quality=st["quality"], detail={"waited_s": waited_s, "blind": st["blind"], "action": "keep_waiting"})
        logger.warning("safety guard: still waiting for headroom >= %.1f C (%s, %.0f s this period)",
                       level, code, waited_s)

    # ------------------------------------------------------------ watchdog
    def tick(self):
        # type: () -> Dict[str, Any]
        """One watchdog observation: sample row, stop rule, warnings."""
        with self._lock:
            now = self._mono()
            fresh = self._fresh_by_key(now)
            candidates, readings_out, worst, blind = self._candidates(fresh, now)
            hottest = min((c for c in candidates if c["headroom_c"] is not None),
                          key=lambda c: c["headroom_c"], default=None)
            self._state = {"min": hottest["headroom_c"] if hottest else None, "quality": worst,
                           "blind": blind, "reading": hottest}
            self._write_sample(now, hottest, worst, readings_out)
            self._evaluate(hottest, blind, worst)
            return dict(self._state)

    def _fresh_by_key(self, now):
        # type: (int) -> Dict[Tuple[str, str], Dict[str, Any]]
        """Fresh readings of this tick; a newly appearing component becomes required."""
        fresh = {}
        for r in self._read_all(now):
            if r["quality"] != "fresh":
                continue
            key = (r["source"], r["component"])
            fresh[key] = r
            if not self.no_source:
                self._required.add(key)
        return fresh

    def _candidates(self, fresh, now):
        # type: (Dict, int) -> Tuple[List[Dict], List[Dict], str, bool]
        """Values that may decide the minimum, every reading for the sample, worst quality, blind flag."""
        candidates, readings_out, worst, blind = [], [], "fresh", False
        for key in sorted(self._required | set(fresh)):
            r = self._observe(key, fresh.get(key), now)
            readings_out.append(r)
            worst = _worse(worst, r["quality"])
            if r["headroom_c"] is not None:
                candidates.append(r)
            blind = blind or r["quality"] == "unavailable"
        if not self._required:
            return candidates, readings_out, "unavailable", True
        return candidates, readings_out, worst, blind

    def _observe(self, key, fresh, now):
        # type: (Tuple[str, str], Optional[Dict], int) -> Dict[str, Any]
        """Fresh value with its rate of rise, else the last value while not stale, else unavailable."""
        last = self._last.get(key)
        if fresh is not None:
            r = dict(fresh, rise_c_per_s=_rise(last, fresh))
            self._last[key] = r
            return r
        if last is not None and now - last["observed_mono_ns"] <= int(self.cfg["stale_after_s"] * 1e9):
            # Kept as a candidate so a vanished hottest sensor can never raise the minimum.
            return dict(last, quality="stale", rise_c_per_s=None)
        return {"source": key[0], "component": key[1], "headroom_c": None, "quality": "unavailable",
                "last_observed_mono_ns": last["observed_mono_ns"] if last else None}

    def _evaluate(self, hottest, blind, worst):
        # type: (Optional[Dict], bool, str) -> None
        """Record a stop-level crossing, blindness and fast rise; the guard never interrupts a run."""
        if self.no_source:
            return
        self._check_crossing(hottest, worst)
        if blind:
            self._on_blind(worst)
        else:
            self._blind_reported = False
        self._check_rise(hottest)

    def _check_crossing(self, hottest, worst):
        # type: (Optional[Dict], str) -> None
        """Headroom below stop_headroom_c: one event per crossing; the run completes, the gate waits after it."""
        stop = self.cfg["stop_headroom_c"]
        below = hottest is not None and hottest["headroom_c"] < stop
        if not below:
            self._crossing_reported = False
            return
        if self._crossing_reported:
            return
        self._crossing_reported = True
        self._emit("warn", value_c=hottest["headroom_c"], threshold_c=stop, reading=hottest, quality=worst,
                   detail={"what": "stop_level_crossed", "active_workloads": self._active, "action": "record_only"})
        logger.warning("safety guard: headroom %.1f C below %.1f C during work (recorded; run continues)",
                       hottest["headroom_c"], stop)

    def _on_blind(self, worst):
        # type: (str) -> None
        """A required source is silent: reported once per silence; admission stays blocked until it answers."""
        if self._blind_reported:
            return
        self._blind_reported = True
        self._emit("source_stale", quality=worst, detail={"active_workloads": self._active})

    def _check_rise(self, hottest):
        # type: (Optional[Dict]) -> None
        """Rate of rise is a warning only until attended traces justify a predictive stop."""
        warn = self.cfg["rise_warn_c_per_s"]
        rise = hottest.get("rise_c_per_s") if hottest else None
        if warn is None or rise is None or rise <= warn:
            self._rise_reported = False
            return
        if not self._rise_reported:
            self._rise_reported = True    # one warning per crossing, not one per second
            self._emit("warn", value_c=rise, threshold_c=warn, reading=hottest, detail={"what": "rise_c_per_s"})

    # ------------------------------------------------------------ records
    def _read_all(self, now):
        # type: (int) -> List[Dict[str, Any]]
        """Readings from every source; a failing source contributes nothing and is logged."""
        out = []
        for src in self._sources:
            try:
                out.extend(src.read(now) or [])
            except Exception as exc:
                logger.warning("safety guard: source %s failed: %s", getattr(src, "name", src), exc)
        return out

    def _write_sample(self, now, hottest, worst, readings_out):
        # type: (int, Optional[Dict], str, List[Dict]) -> Dict[str, Any]
        """One sample row per tick; GPU temperature and power stay NULL until a source supplies them."""
        self._sample_seq += 1
        h = hottest or {}
        row = {
            "episode_id": self.episode["episode_id"], "seq": self._sample_seq,
            "utc_ns": self._utc(), "mono_ns": now,
            "min_headroom_c": h.get("headroom_c"), "min_source": h.get("source"),
            "min_component": h.get("component"), "min_limit_type": h.get("limit_type"),
            "min_quality": worst,
            "gpu_temp_c": None, "gpu_power_mw": None, "gpu_power_limit_mw": None,
            "clock_reasons_bitmask": (h.get("raw") or {}).get("clocks_event_reasons"),
            "rise_c_per_s": h.get("rise_c_per_s"),
            "readings_json": json.dumps(readings_out, sort_keys=True, default=str),
        }
        self._store_sample(row)
        return row

    def _store_sample(self, row):
        # type: (Dict[str, Any]) -> None
        """Hand the sample to the store sink; a failure is counted, never raised."""
        if self._store is None:
            return
        try:
            self._store.store_sample(row)
        except Exception as exc:
            self.unstored_samples += 1
            logger.error("safety guard: sample not stored: %s", exc)

    def _emit(self, event_type, value_c=None, threshold_c=None, reading=None, quality=None,
              links=None, detail=None):
        # type: (str, Optional[float], Optional[float], Optional[Dict], Optional[str], Optional[Dict], Optional[Dict]) -> Dict[str, Any]
        """Record one event: host log first (survives a crash), then the store."""
        with self._lock:
            self._event_seq += 1
            ev = self._event_row(event_type, value_c, threshold_c, reading or {}, quality, links or {}, detail)
            ev["log_written"] = 1 if self._append_log(ev) else 0
            self._store_event(ev)
            return ev

    def _event_row(self, event_type, value_c, threshold_c, rd, quality, links, detail):
        # type: (str, Optional[float], Optional[float], Dict, Optional[str], Dict, Optional[Dict]) -> Dict[str, Any]
        """Event fields; links are plain ids (an event can precede its run row)."""
        return {
            "event_id": str(uuid.uuid4()), "episode_id": self.episode["episode_id"],
            "seq": self._event_seq, "event_type": event_type,
            "utc_ns": self._utc(), "mono_ns": self._mono(),
            "value_c": value_c, "threshold_c": threshold_c,
            "source": rd.get("source"), "component": rd.get("component"),
            "limit_type": rd.get("limit_type"), "quality": quality,
            "exp_id": links.get("exp_id"), "run_id": links.get("run_id"),
            "goal_id": links.get("goal_id"), "attempt_no": links.get("attempt_no"),
            "seq_attempt_id": links.get("seq_attempt_id"),
            "te_session_id": links.get("te_session_id", self.episode["te_session_id"]),
            "detail_json": json.dumps(detail, sort_keys=True, default=str) if detail else None,
        }

    def _append_log(self, ev):
        # type: (Dict[str, Any]) -> bool
        """Append the event to the host log; False (never an exception) when that fails."""
        try:
            return bool(self._log(dict(ev, record="safety_guard_event")))
        except Exception as exc:
            logger.error("safety guard: host log append failed: %s", exc)
            return False

    def _store_event(self, ev):
        # type: (Dict[str, Any]) -> None
        """Hand the event to the store sink; a failure keeps it for reconciliation by event_id."""
        if self._store is None:
            return
        try:
            self._store.store_event(ev)
        except Exception as exc:
            self.unstored_events.append(ev)
            logger.error("safety guard: event %s not stored: %s", ev["event_id"], exc)

    def _call_store(self, method, payload):
        # type: (str, Dict[str, Any]) -> None
        """Episode insert or update; a failure is logged, never raised into the safety path."""
        if self._store is None:
            return
        try:
            getattr(self._store, method)(payload)
        except Exception as exc:
            logger.error("safety guard: %s failed: %s", method, exc)


def _rise(prev, cur):
    # type: (Optional[Dict], Dict) -> Optional[float]
    """Temperature rise in C/s as the drop in headroom between two fresh readings; None without a pair."""
    if prev is None or prev.get("headroom_c") is None:
        return None
    dt = cur["observed_mono_ns"] - prev["observed_mono_ns"]
    if dt <= 0:
        return None                     # same observation time: no rate can be computed
    return (prev["headroom_c"] - cur["headroom_c"]) / (dt / 1e9)


class JsonlEventLog(object):
    """Append one JSON line per event and force it to disk; returns False on failure."""

    def __init__(self, path):
        # type: (Optional[str]) -> None
        """path None means no host log directory was found; every append then reports False."""
        self.path = path

    def __call__(self, record):
        # type: (Dict[str, Any]) -> bool
        """Append and fsync, so the line survives a power loss right after the call."""
        if not self.path:
            return False
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError as exc:
            logger.error("safety guard: cannot append to %s: %s", self.path, exc)
            return False
