"""
Idle reference, cooling and recording checks for one recording
(thermal_exposure extension).

The recorder owns the threads, the time line and the writer; this class
owns the idle reference, the cooling rule and the recording checks, and
is called by the recorder at fixed points:

  prepare()          at open, before sampling starts: settings, sensor and
                     channel names from te_sensor / te_channel, previous
                     recording (carry-over), energy counter mapping
  wait_pre_window()  replaces the fixed pre-window wait; with idle.measure it
                     extends the window until the stability rules pass
  observe()          every sample row (recorder lock held; must stay cheap)
  energy_active()    energy counters are read only inside idle windows
  run_cooldown()     cooling-complete rule after the last run
  run_post_window()  post idle window after a completed cool-down
  write_results()    te_idle_window rows and te_session evidence columns
  check_rows()       rows for te_session_check

Names come from the machine profile tables, so the same code serves every
platform; a sensor, channel or tool that does not exist gives 'unknown',
never 'pass' and never 0. Rules and thresholds live in idle_checks.py.
Imports nothing from core (extension rule).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from extensions.thermal_exposure import gpu_users as gu
from extensions.thermal_exposure import idle_checks as ic

logger = logging.getLogger(__name__)

# extra provisional defaults (same rule as idle_checks.DEFAULTS)
EXTRA_DEFAULTS: Dict[str, float] = {
    "min_valid_share": 0.98,     # share of VALID rows per main sensor and channel
    "room_change_c": 1.0,        # room temperature change that warns
    "gpu_check_s": 5.0,          # GPU process checkpoint interval between runs
    "energy_hz": 10.0,           # energy counter reads inside idle windows
    "power_resolution_mw": 1.0,  # below power_floor_factor x this: rel. MAD unknown
}

# cooldown section key -> flat rule key
_COOL_KEYS = {"tolerance_c": "cool_tolerance_c", "mad_k": "cool_mad_k",
              "hold_s": "cool_hold_s", "max_s": "cool_max_s"}

# reader row keys per recorder source: (series kind, key field, value field)
_SERIES = {"THERMAL_ZONE": ("zone", "zone_id", "temp_celsius"),
           "GPU_TEMP": ("gpu", "gpu_index", "temperature_c"),
           "POWER_RAIL": ("rail", "rail_id", "power_mw"),
           "ENERGY": ("energy", "domain_id", "energy_uj"),
           "AMBIENT": ("amb", "sensor_name", "temp_celsius")}


class IdleSession:
    """Per-recording idle reference and checks; see the module docstring."""

    def __init__(self, recorder: Any, settings: Dict[str, Any]) -> None:
        self.rec = recorder
        self.raw = dict(settings or {})
        idle = dict(self.raw.get("idle") or {})
        self.measure = bool(idle.get("measure", False))
        self.cfg = self._merge_cfg(idle)
        self.server = dict(self.raw.get("server") or {})
        self.sha = hashlib.sha256(json.dumps(
            {"cfg": self.cfg, "measure": self.measure, "server": self.server,
             "decision": idle.get("decision_sensor"), "guards": idle.get("guard_sensors")},
            sort_keys=True).encode()).hexdigest()
        self._decision_req = idle.get("decision_sensor")
        self._guard_req = idle.get("guard_sensors")
        self._phase: Optional[str] = None        # 'cool' or 'post' while running
        self._data: Dict[str, Dict[Tuple[str, Any], List[Tuple[int, float]]]] = \
            {"pre": {}, "post": {}}
        self._counts: Dict[Tuple[str, Any], List[int]] = {}   # key -> [valid, total]
        self._names: Dict[Tuple[str, Any], Tuple[str, str]] = {}  # key -> (name, kind)
        self._res: Dict[Tuple[str, Any], float] = {}
        self.decision: Optional[Tuple[str, Any]] = None
        self.guards: List[Tuple[str, Any]] = []
        self.main_channel: Optional[Tuple[str, Any]] = None
        self._hold: Optional[ic.CoolingHold] = None
        self._hold_bands: Dict[str, Tuple[float, float, float]] = {}
        self._cool: Dict[str, Any] = {"status": None, "seconds": None}
        self._pre_result: Optional[Dict[str, Any]] = None
        self.equilibrium: Optional[int] = None
        self.post_start: Optional[int] = None
        self.prev: Dict[str, Any] = {"id": None, "contaminated": None}
        self.baseline_id: Optional[str] = None
        self._snaps: List[Optional[List[Dict[str, Any]]]] = []
        self._origin: Dict[str, Dict[str, Any]] = {}    # te_sensor resolution origin by name
        self._own: set = set()

    # ── settings ────────────────────────────────────────────────────────────

    def _merge_cfg(self, idle: Dict[str, Any]) -> Dict[str, float]:
        """Flat rule settings: defaults, then idle, checks and cooldown sections."""
        cfg = dict(ic.DEFAULTS)
        cfg.update(EXTRA_DEFAULTS)
        cfg.update({k: float(v) for k, v in idle.items() if k in cfg})
        checks = self.raw.get("checks") or {}
        cfg.update({k: float(v) for k, v in checks.items() if k in cfg})
        # recorder cooldown (built by the platform from the same section) then settings
        cool = dict(getattr(self.rec, "cooldown", None) or {})
        cool.update(self.raw.get("cooldown") or {})
        cfg.update({_COOL_KEYS[k]: float(v) for k, v in cool.items()
                    if k in _COOL_KEYS and v is not None})
        cfg["post_s"] = float(idle.get("post_s", cfg["pre_s"]))
        cfg["carryover_window_s"] = float(idle.get("carryover_window_s", cfg["cool_max_s"]))
        return cfg

    # ── open ────────────────────────────────────────────────────────────────

    def prepare(self) -> None:
        """Resolve names, previous recording and energy mapping. Never raises."""
        try:
            self._resolve_names()
            self._find_previous()
            self._map_energy()
            if self.measure:
                self.rec.pre_window_s = self.cfg["pre_s"]   # planned length for the span
            else:
                self.baseline_id = self._latest_baseline()
            self._own = gu.own_pids(self.server.get("process_names") or [],
                                    self.server.get("pids") or [])
        except Exception as exc:
            logger.warning("idle session prepare failed (checks will be unknown): %s", exc)
        self.rec._spawn(self._gpu_loop, None, "rec-gpu-users")

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Read rows through the writer; [] (logged) when the table or column is missing."""
        w = self.rec.writer_factory()
        w.open()
        try:
            return [dict(r) for r in (w.execute(sql, params) or [])]
        except Exception as exc:
            logger.info("idle session read skipped: %s", exc)
            return []
        finally:
            w.close()

    def _resolve_names(self) -> None:
        """Sensor and channel keys from te_sensor / te_channel and thermal_zones."""
        zones = {(z["zone_type"], z["zone_index"]): z["zone_id"] for z in self._query(
            "SELECT zone_id, zone_type, zone_index FROM thermal_zones WHERE active = 1")}
        main_sensor = None
        for s in self._query("SELECT sensor_name, source_table, zone_type, zone_index, "
                             "gpu_index, nominal_resolution_c, is_main FROM te_sensor"):
            key = self._sensor_key(s, zones)
            if key is None:
                continue
            self._names[key] = (s["sensor_name"], "sensor")
            self._res[key] = float(s["nominal_resolution_c"])
            main_sensor = key if s["is_main"] else main_sensor
        for c in self._query("SELECT channel_name, source_table, source_key, is_main "
                             "FROM te_channel"):
            key = self._channel_key(c)
            self._names[key] = (c["channel_name"], "channel")
            self.main_channel = key if c["is_main"] else self.main_channel
        self._pick_decision(main_sensor)
        self._load_resolution_origin()

    def _load_resolution_origin(self) -> None:
        """observed_step_c, status and catalog per sensor name (absent before e004)."""
        rows = self._query("SELECT sensor_name, observed_step_c, resolution_status, catalog_sha256 "
                           "FROM te_sensor")
        self._origin = {r["sensor_name"]: r for r in rows}

    @staticmethod
    def _sensor_key(s: Dict[str, Any], zones: Dict[Tuple[str, int], int]) -> Optional[Tuple[str, Any]]:
        """Series key of a profile sensor, or None when it is not on this machine."""
        if s["source_table"] == "thermal_samples_v2":
            zid = zones.get((s["zone_type"], s["zone_index"]))
            return ("zone", zid) if zid is not None else None
        if s["source_table"] == "gpu_samples":
            return ("gpu", int(s["gpu_index"] or 0))
        return ("amb", s["sensor_name"])

    @staticmethod
    def _channel_key(c: Dict[str, Any]) -> Tuple[str, Any]:
        """Series key of a profile channel (rail id, energy domain, or GPU)."""
        src, key = c["source_table"], str(c["source_key"])
        if src == "power_rail_samples":
            return ("rail", int(key))
        if src == "energy_sample_domains":
            return ("energy", int(key) if key.isdigit() else key)
        return ("gpu_power", key)        # not recorded between runs (known gap)

    def _pick_decision(self, main_sensor: Optional[Tuple[str, Any]]) -> None:
        """Decision sensor (setting, else profile main sensor) and guard sensors."""
        by_name = {n: k for k, (n, kind) in self._names.items() if kind == "sensor"}
        self.decision = by_name.get(self._decision_req) if self._decision_req else main_sensor
        if self._decision_req and self.decision is None:
            logger.warning("decision sensor %s not on this machine", self._decision_req)
        guards = self._guard_req if self._guard_req is not None else \
            ([self._names[main_sensor][0]] if main_sensor else [])
        self.guards = [by_name[g] for g in guards if g in by_name and by_name[g] != self.decision]

    def _find_previous(self) -> None:
        """Carry-over: did the previous recording on this node cool down?"""
        rows = self._query(
            "SELECT session_id, status, cooldown_status, cooling_complete, start_ns, end_ns "
            "FROM te_session WHERE node_name = ? AND session_id < ? "
            "ORDER BY session_id DESC LIMIT 1", (self.rec.node_name, self.rec.session_id))
        if not rows:
            return
        p = rows[0]
        cooled = p["cooldown_status"] == "completed" or p["cooling_complete"] == 1
        ended = p["end_ns"] or p["start_ns"]
        recent = self.rec._base_wall - ended < self.cfg["carryover_window_s"] * 1e9
        self.prev = {"id": p["session_id"], "contaminated": int(not cooled and recent)}

    def _resolve_energy_channel(self) -> None:
        """A main channel given as an energy domain name becomes its domain_id."""
        mc = self.main_channel
        if mc is None or mc[0] != "energy" or isinstance(mc[1], int):
            return
        rows = self._query("SELECT domain_id FROM energy_domains WHERE name = ?", (mc[1],))
        name = self._names.pop(mc, (str(mc[1]), "channel"))
        self.main_channel = ("energy", rows[0]["domain_id"]) if rows else None
        if self.main_channel:
            self._names[self.main_channel] = name

    def _latest_baseline(self) -> Optional[str]:
        """Id of the store idle baseline that BaselineManager would reuse (read only)."""
        rows = self._query("SELECT baseline_id FROM idle_baselines "
                           "ORDER BY timestamp DESC LIMIT 1")
        return str(rows[0]["baseline_id"]) if rows else None

    def _map_energy(self) -> None:
        """Keep the energy reader only when the main channel is an energy counter."""
        rec = self.rec
        if "ENERGY" not in rec.readers:
            return
        doms = self._query("SELECT domain_id, reader_keys FROM energy_domains "
                           "WHERE is_cumulative = 1 AND reader_keys IS NOT NULL")
        keymap = {k.strip(): d["domain_id"] for d in doms for k in d["reader_keys"].split(",")}
        self._resolve_energy_channel()
        wanted = self.measure and self.main_channel is not None and self.main_channel[0] == "energy"
        if not wanted or not keymap:
            _drop_source(rec, "ENERGY")   # power rails or no counters: never read
            return
        raw = rec.readers["ENERGY"]
        rec.readers["ENERGY"] = lambda: [
            dict(r, domain_id=keymap[r["key"]]) for r in raw() if r.get("key") in keymap]

    # ── sampling hooks ──────────────────────────────────────────────────────

    def phase(self) -> Optional[str]:
        """'pre' before the first run, 'cool' / 'post' while those run, else None."""
        if self._phase:
            return self._phase
        return "pre" if self.rec._workload_start is None else None

    def energy_active(self) -> bool:
        """Energy counters are read only inside idle windows (pre and post)."""
        return self.measure and self.phase() in ("pre", "post")

    def observe(self, source: str, row: Dict[str, Any], ts: int) -> None:
        """
        One sample row (recorder lock held). Counts validity for every row;
        keeps VALID values of idle windows and feeds the cooling hold.
        """
        spec = _SERIES.get(source)
        if spec is None:
            return
        key = (spec[0], row.get(spec[1]))
        cnt = self._counts.setdefault(key, [0, 0])
        cnt[1] += 1
        value = row.get(spec[2])
        if (row.get("quality_flag") or "VALID") != "VALID" or value is None:
            return
        cnt[0] += 1
        ph = self.phase()
        if ph in self._data:
            self._data[ph].setdefault(key, []).append((ts, float(value)))
        if ph == "cool" and self._hold is not None and key in self._names:
            self._hold.feed(self._names[key][0], ts, float(value))

    def _series(self, phase: str, key: Optional[Tuple[str, Any]],
                since: int = 0) -> List[Tuple[int, float]]:
        """Copy of one idle-window series from 'since' (takes the recorder lock)."""
        if key is None:
            return []
        with self.rec._lock:
            return [p for p in self._data[phase].get(key, []) if p[0] >= since]

    def _rate(self, key: Optional[Tuple[str, Any]]) -> float:
        """Requested rate of the recorder source behind a series key."""
        src = {"zone": "THERMAL_ZONE", "gpu": "GPU_TEMP", "rail": "POWER_RAIL",
               "energy": "ENERGY", "amb": "AMBIENT"}.get(key[0] if key else "", "")
        return float(self.rec.rates.get(src, 0.0))

    # ── pre idle window ─────────────────────────────────────────────────────

    def wait_pre_window(self) -> None:
        """Fixed pre-window, or (idle.measure) extend until the stability rules pass."""
        if not self.measure:
            self.rec._stop.wait(self.rec.pre_window_s)
            return
        target, cap, t0 = self.cfg["pre_s"], self.cfg["pre_max_s"], time.monotonic()
        while not self.rec._stop.is_set():
            self.rec._stop.wait(max(0.0, target - (time.monotonic() - t0)))
            res = self.assess_pre()
            logger.info("pre idle window %.0f s: %s", target,
                        "stable" if res["ok"] else "not yet: %s" % "; ".join(res["reasons"]))
            if res["ok"] or target >= cap:
                break
            target = min(cap, target + self.cfg["extend_step_s"])
        self.equilibrium = int(bool(self._pre_result and self._pre_result["ok"]))
        self.rec.pre_window_s = target

    def assess_pre(self) -> Dict[str, Any]:
        """Acceptance of the pre window so far (decision, guards, main channel)."""
        end = self.rec._ts()
        win, reasons = (self.rec._base_wall, end), []
        named = [("decision sensor", self.decision), ("main channel", self.main_channel)]
        missing = [what for what, key in named if key is None]
        reasons += ["no %s" % what for what in missing]
        for key in [k for k in [self.decision] + self.guards + [self.main_channel] if k]:
            ser = self._series("pre", key)
            r = ic.assess_series([t for t, _ in ser], win, self._rate(key), self.cfg)
            reasons += ["%s: %s" % (self._label(key), x) for x in r["reasons"]]
        stab = self._stability(end)
        if stab["slope_ok"] is not True:
            reasons.append("slope bound %s C/min" % _f(stab["slope_bound"]))
        if stab["power_ok"] is False:
            reasons.append("power rel. MAD %s" % _f(stab["power_rel_mad"]))
        self._pre_result = dict(stab, ok=not reasons, reasons=reasons)
        return self._pre_result

    def _stability(self, end: int) -> Dict[str, Any]:
        """Slope of the decision sensor and spread of the main channel, final interval."""
        since = end - int(self.cfg["final_s"] * 1e9)
        temps = self._series("pre", self.decision, since)
        res = self._res.get(self.decision, 0.0) if self.decision else 0.0
        return ic.assess_stability([t for t, _ in temps], [v for _, v in temps], res,
                                   self._powers("pre", since), self.cfg["power_resolution_mw"],
                                   self.cfg)

    def _powers(self, phase: str, since: int = 0) -> List[float]:
        """Main channel power values in mW (rails direct, counters per interval)."""
        ser = self._series(phase, self.main_channel, since)
        if self.main_channel and self.main_channel[0] == "energy":
            return ic.interval_power_mw(ser)
        return [v for _, v in ser]

    # ── other GPU users ─────────────────────────────────────────────────────

    def _gpu_loop(self, _arg: Any = None) -> None:
        """NVML process checkpoints between runs only (read only)."""
        nvml = gu.load_nvml()
        while True:
            self._take_snapshot(nvml)
            if self.rec._stop.wait(self.cfg["gpu_check_s"]):
                break
            if not self.rec._active.is_set():
                self.rec._active.wait()          # sleeps through the run; next read after it
        _nvml_shutdown(nvml)

    def _take_snapshot(self, nvml: Any) -> None:
        """One checkpoint, unless a run is in progress."""
        if self.rec._paused:
            return
        snap = gu.snapshot(nvml)
        with self.rec._lock:
            self._snaps.append(snap)

    # ── cooling and post window ─────────────────────────────────────────────

    def run_cooldown(self) -> str:
        """
        Cooling-complete rule after the last run. Returns the cooldown status:
        not_requested, completed or timed_out (a timeout is never cooled).
        """
        max_s = float((self.rec.cooldown or {}).get("max_s") or 0.0)
        if max_s <= 0:
            return self._cool_done("not_requested", None)
        bands = self._bands()
        if not bands:
            logger.warning("cool-down requested but no pre-window reference")
            return self._cool_done("timed_out", 0.0)
        t0 = time.monotonic()
        with self.rec._lock:
            self._hold = ic.CoolingHold(bands, self.cfg["cool_hold_s"])
            self._hold_bands, self._phase = bands, "cool"
        status = "timed_out"
        while time.monotonic() - t0 < max_s:
            time.sleep(0.2)
            if self._hold.complete(self.rec._ts()):
                status = "completed"
                break
        with self.rec._lock:
            self._phase = None
        logger.info("cool-down %s after %.0f s (hold restarts %d)", status,
                    time.monotonic() - t0, self._hold.resets)
        return self._cool_done(status, time.monotonic() - t0)

    def _cool_done(self, status: str, seconds: Optional[float]) -> str:
        """Remember the cooling outcome for results and checks."""
        self._cool = {"status": status, "seconds": seconds}
        return status

    def _bands(self) -> Dict[str, Tuple[float, float, float]]:
        """Band per decision and guard sensor from its pre median, MAD and resolution."""
        keys = [k for k in [self.decision] + self.guards if k is not None]
        if self.decision is None:
            keys = [k for k in self._data["pre"] if k[0] == "zone"]   # no profile: all zones
        bands = {}
        for key in keys:
            med, mad = ic.median_mad([v for _, v in self._series("pre", key)])
            rate = self._rate(key)
            if med is None or rate <= 0:
                continue
            bands[self._label(key)] = (med, ic.cooling_band(mad, self._res.get(key, 0.0), self.cfg),
                                       1.0 / rate)
        return bands

    def band_c(self) -> Optional[float]:
        """Band of the decision sensor (span attribute), or None."""
        b = self._hold_bands.get(self._label(self.decision)) if self.decision else None
        if b:
            return b[1]
        # no band computed (no reference yet): the engineering tolerance, if a
        # cool-down was requested at all
        return self.cfg["cool_tolerance_c"] if (self.rec.cooldown or {}).get("max_s") else None

    def run_post_window(self, cooldown_status: str) -> Optional[int]:
        """Post idle window after a completed cool-down; returns its start, or None."""
        if not self.measure or cooldown_status != "completed":
            return None
        if self.rec._workload_start is None:
            return None                  # no run: the whole recording is the pre window
        with self.rec._lock:
            self._phase = "post"
            self.post_start = self.rec._ts()
        self.rec._stop.wait(self.cfg["post_s"])
        with self.rec._lock:
            self._phase = None
        return self.post_start

    # ── results ─────────────────────────────────────────────────────────────

    def write_results(self, end_ns: int) -> None:
        """te_idle_window rows and the te_session evidence columns."""
        rows = self._window_rows("pre", self.rec._base_wall,
                                 self.rec._workload_start or end_ns)
        if self.post_start is not None:
            rows += self._window_rows("post", self.post_start, end_ns)
        if rows:
            self.rec._write_rows(
                "INSERT INTO te_idle_window (session_id, phase, channel_name, sensor_name, "
                "median_value, mad_value, unit, sample_count, start_ns, end_ns, method_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        self.rec._write_rows(
            "UPDATE te_session SET idle_reference = ?, idle_baseline_id = ?, "
            "idle_equilibrium = ?, declared_idle_state = ?, idle_state_verification = ?, "
            "carryover_contaminated = ?, prev_session_id = ?, decision_sensor = ?, "
            "checks_config_sha256 = ?, drift_session = ?, cooling_complete = ?, "
            "ambient_status = ? WHERE session_id = ?",
            [("matched" if self.measure else "store_baseline", self.baseline_id,
              self.equilibrium, self.server.get("state"), self._idle_state()["result"],
              self.prev["contaminated"], self.prev["id"],
              self._label(self.decision) if self.decision else None, self.sha,
              self._drift_flag(), _cool_flag(self._cool["status"]),
              "available" if self._ambient_values() else "unavailable",
              self.rec.session_id)])

    def _window_rows(self, phase: str, start: int, end: int) -> List[tuple]:
        """One te_idle_window row per series with VALID samples in the window."""
        out = []
        for key in list(self._data[phase]):
            vals, unit, method = self._window_values(phase, key)
            med, mad = ic.median_mad(vals)
            if med is None:
                continue
            name = self._label(key)
            ch, se = (name, None) if unit == "mW" else (None, name)
            out.append((self.rec.session_id, phase, ch, se, med, mad, unit, len(vals),
                        start, max(start, end), method))
        return out

    def _window_values(self, phase: str, key: Tuple[str, Any]) -> Tuple[List[float], str, str]:
        """Values, unit and method id of one series in one idle window."""
        ser = self._series(phase, key)
        if key[0] == "energy":
            return ic.interval_power_mw(ser), "mW", "idle_power_from_counter_v1"
        unit = "mW" if key[0] == "rail" else "C"
        return [v for _, v in ser], unit, "idle_window_median_v1"

    def _median(self, phase: str, key: Optional[Tuple[str, Any]]) -> Optional[float]:
        """Median of one series in one window (power from counters converted)."""
        if key is None:
            return None
        return ic.median_mad(self._window_values(phase, key)[0])[0]

    # ── checks ──────────────────────────────────────────────────────────────

    def check_rows(self) -> List[tuple]:
        """Rows (session_id, name, result, value, threshold, note, method_id)."""
        sid = self.rec.session_id
        rows = [self._pre_check(), self._cool_check(), self._ambient_check(),
                self._carryover_check()]
        rows += self._drift_checks() + self._valid_checks() + self._gpu_checks()
        rows += self._resolution_checks()
        return [(sid,) + r for r in rows]

    def _pre_check(self) -> tuple:
        """Pre idle window acceptance (unknown when not measured)."""
        if not self.measure:
            return ("idle_pre_acceptance", "unknown", None, None,
                    "not_assessed (idle.measure false; store idle baseline %s)" % self.baseline_id,
                    "idle_pre_acceptance_v1")
        r = self._pre_result or {"ok": False, "reasons": ["not assessed"], "slope_bound": None}
        return ("idle_pre_acceptance", "pass" if r["ok"] else "fail", r.get("slope_bound"),
                self.cfg["slope_limit_c_per_min"],
                ("window %.0f s; " % self.rec.pre_window_s + "; ".join(r["reasons"]))[:200],
                "idle_pre_acceptance_v1")

    def _cool_check(self) -> tuple:
        """Cooling reached: pass completed, fail timed out, unknown otherwise."""
        st = self._cool["status"]
        result = {"completed": "pass", "timed_out": "fail"}.get(st, "unknown")
        resets = self._hold.resets if self._hold else 0
        note = "status %s; decision %s; guards %s; hold restarts %d" % (
            st, self._label(self.decision) if self.decision else "all zones",
            [self._label(g) for g in self.guards], resets)
        return ("cooling_reached", result, self._cool["seconds"], self.band_c(), note[:200],
                "cooling_complete_v1")

    def _drift_checks(self) -> List[tuple]:
        """Idle drift for the decision sensor, guards and main channel."""
        out = []
        for key in [k for k in [self.decision] + self.guards if k is not None]:
            res, diff = ic.drift(self._median("pre", key), self._median("post", key),
                                 self.cfg["drift_tolerance_c"])
            out.append(("idle_drift_%s" % self._label(key), res, diff,
                        self.cfg["drift_tolerance_c"], "C, post - pre median", "idle_drift_v1"))
        if self.main_channel is not None:
            res, diff = ic.drift(self._median("pre", self.main_channel),
                                 self._median("post", self.main_channel),
                                 self.cfg["drift_tolerance_rel"], relative=True)
            out.append(("idle_drift_%s" % self._label(self.main_channel), res, diff,
                        self.cfg["drift_tolerance_rel"], "mW, post - pre median; "
                        "threshold is a share of pre", "idle_drift_v1"))
        return out

    def _drift_flag(self) -> Optional[int]:
        """te_session.drift_session: 1 any drift, 0 all pass, None unknown."""
        results = [r[1] for r in self._drift_checks()]
        if not results or "unknown" in results:
            return None
        return int("warn" in results)

    def _valid_checks(self) -> List[tuple]:
        """Share of VALID rows between runs for the main sensors and channel."""
        out = []
        for key in [k for k in [self.decision] + self.guards + [self.main_channel] if k]:
            valid, total = self._counts.get(key, [0, 0])
            share = valid / total if total else None
            res = "unknown" if share is None else (
                "pass" if share >= self.cfg["min_valid_share"] else "fail")
            out.append(("valid_share_%s" % self._label(key), res, share,
                        self.cfg["min_valid_share"], "%d of %d rows VALID" % (valid, total),
                        "experiment_recording_v1"))
        return out

    def _ambient_values(self) -> List[float]:
        """VALID room temperatures between runs (all windows) seen by observe()."""
        cnt = [k for k in self._counts if k[0] == "amb" and self._counts[k][0] > 0]
        vals = [v for ph in self._data.values() for k in cnt for _, v in ph.get(k, [])]
        return vals if cnt else []

    def _ambient_check(self) -> tuple:
        """Room temperature change, or the probe recorded as unavailable."""
        vals = self._ambient_values()
        if not vals:
            return ("room_temperature", "unknown", None, self.cfg["room_change_c"],
                    "room probe unavailable (recorded, not zero)", "experiment_recording_v1")
        change = max(vals) - min(vals)
        return ("room_temperature", "warn" if change > self.cfg["room_change_c"] else "pass",
                change, self.cfg["room_change_c"], "C, max - min in idle windows",
                "experiment_recording_v1")

    def _carryover_check(self) -> tuple:
        """Previous recording on this node cooled down (separate evidence)."""
        c = self.prev["contaminated"]
        res = "unknown" if c is None else ("warn" if c else "pass")
        return ("carryover", res, float(c) if c is not None else None, 0.0,
                "previous session %s" % self.prev["id"], "cooling_complete_v1")

    def _idle_state(self) -> Dict[str, Any]:
        """Declared idle state checked against the checkpoints."""
        with self.rec._lock:
            snaps = list(self._snaps)
        return gu.verify_idle_state(self.server.get("state"), snaps,
                                    self.server.get("process_names") or [])

    def _gpu_checks(self) -> List[tuple]:
        """External GPU activity and processes at checkpoints; runs not observable."""
        with self.rec._lock:
            snaps = list(self._snaps)
        c = gu.classify(snaps, self._own)
        note = "%d checkpoints, external pids %s; no external activity at checkpoints " \
               "is not proof of absence during runs" % (c["checkpoints"], c["external"][:10])
        st = self._idle_state()
        return [
            ("gpu_external_activity", c["activity"], float(len(c["external"])), 0.0,
             note[:200], "experiment_recording_v1"),
            ("gpu_external_process", c["process"], float(len(c["external"])), 0.0,
             note[:200], "experiment_recording_v1"),
            ("gpu_interference_inside_runs", "unknown", None, None,
             "not observable inside runs (recorder paused)", "experiment_recording_v1"),
            ("idle_state_verification", st["result"], None, None,
             ("declared %s; %s" % (self.server.get("state"), st["note"]))[:200],
             "experiment_recording_v1"),
        ]

    def _resolution_checks(self) -> List[tuple]:
        """
        Step seen in this recording's idle windows against the catalog's observed
        step (like with like), and the catalog against the current hw_config.
        A finer step than the catalog means the catalog is stale or too short.
        """
        from extensions.thermal_exposure.profile_loader import load_sensor_facts
        current = load_sensor_facts().get("sha")
        out = []
        for key in [k for k in [self.decision] + self.guards if k is not None]:
            name = self._label(key)
            origin = self._origin.get(name) or {}
            seen = _recorded_step([v for ph in self._data.values() for _, v in ph.get(key, [])])
            out.append(_resolution_row(name, origin, seen, current))
        return out

    def _label(self, key: Optional[Tuple[str, Any]]) -> str:
        """Profile name of a series key, or kind:id when the profile has none."""
        if key is None:
            return "none"
        return self._names.get(key, ("%s:%s" % key, ""))[0]


def _drop_source(rec: Any, name: str) -> None:
    """Remove a source before sampling starts (never read)."""
    for d in (rec.readers, rec.rates, rec._buffers, rec._stats, rec._last_keys, rec._fail_counts):
        d.pop(name, None)


def _cool_flag(status: Optional[str]) -> Optional[int]:
    """te_session.cooling_complete: 1 completed, 0 timed out, None otherwise."""
    return {"completed": 1, "timed_out": 0}.get(status or "")


def _nvml_shutdown(nvml: Any) -> None:
    """Release NVML if it was opened."""
    if nvml is None:
        return
    try:
        nvml.nvmlShutdown()
    except Exception as exc:
        logger.debug("NVML shutdown: %s", exc)


def _f(v: Optional[float]) -> str:
    """Short number text, 'n/a' for None."""
    return "n/a" if v is None else "%.3f" % v


def _recorded_step(values: List[float]) -> Optional[float]:
    """Smallest step between consecutive VALID values seen at least twice, or None."""
    counts: Dict[float, int] = {}
    for a, b in zip(values, values[1:]):
        step = round(abs(b - a), 6)
        if step > 0:
            counts[step] = counts.get(step, 0) + 1
    reliable = [s for s, n in counts.items() if n >= 2]
    return min(reliable) if reliable else None


def _resolution_row(name: str, origin: Dict[str, Any], seen: Optional[float],
                    current_sha: Optional[str]) -> tuple:
    """One sensor_resolution_<name> check row (without session id)."""
    cat_step, status, cat_sha = (origin.get("observed_step_c"), origin.get("resolution_status"),
                                 origin.get("catalog_sha256"))
    notes = ["recording step %s, catalog step %s (%s)" % (seen, cat_step, status)]
    result = "unknown" if seen is None or cat_step is None else "pass"
    if result == "pass" and seen < cat_step - 1e-9:
        result = "warn"
        notes.append("finer than the catalog: rerun scripts/detect_thermal.py")
    if cat_sha and current_sha and cat_sha != current_sha:
        result = "warn"
        notes.append("hw_config catalog changed since the profile was loaded; reload the profile")
    return ("sensor_resolution_%s" % name, result, seen, cat_step, "; ".join(notes)[:200],
            "sensor_resolution_observed_v1")
