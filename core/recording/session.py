"""
Open and close a continuous experiment recording.

Builds the platform readers exactly as the harness does (same discovery,
registration and ReaderFactory calls, PAC-2), a writer factory on the
resolved store (one short-lived writer per write, so the recorder never holds
the store lock while a run is saving), and loads the recorder class from the
entry point group alems.recorders (core never imports an extension directly).
"""

import atexit
import json
import logging
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.recording import hook

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "alems.recorders"
_HW_CONFIG = pathlib.Path(__file__).resolve().parent.parent.parent / "config" / "hw_config.json"


def _writer_factory() -> Callable[[], Any]:
    """Factory of fresh writers on the resolved store (store resolver only)."""
    from core.storage.inprocess_writer import InProcessWriter
    from core.storage.resolver import resolve_store
    store = resolve_store()
    logger.info("recording store: %s", store)
    return lambda: InProcessWriter(store)


def _thermal_and_cooling() -> Tuple[Optional[Any], Optional[Any]]:
    """ThermalReaderV2 and CoolingReader built as in harness.__init__."""
    from core.thermal.thermal_discovery import (
        discover_thermal_zones, discover_cooling_devices,
        register_thermal_zones, register_cooling_devices, get_machine_id)
    from core.readers.factory import ReaderFactory as _RF
    from core.storage.inprocess_writer import InProcessWriter
    from core.storage.resolver import resolve_store
    machine_id = get_machine_id()
    w = InProcessWriter(resolve_store())
    w.open()
    try:
        zones = register_thermal_zones(w.conn, discover_thermal_zones(machine_id))
        devices = register_cooling_devices(w.conn, discover_cooling_devices(machine_id))
    finally:
        w.close()
    thermal = _RF._make_thermal_reader_v2(zones) if zones else None
    cooling = _RF._make_cooling_reader(devices) if devices else None
    return thermal, cooling


def _power_rails() -> Optional[Any]:
    """PowerRailSampler on the hw_config power paths (machines with rails only)."""
    try:
        paths = json.loads(_HW_CONFIG.read_text()).get("spbm", {}).get("power_paths", {})
    except (OSError, ValueError) as exc:
        logger.info("recording: no power rail config (%s)", exc)
        return None
    if not paths:
        return None
    from core.readers.power_rail_sampler import PowerRailSampler
    sampler = PowerRailSampler(paths, hz=10)
    return sampler if sampler.is_available() else None


def _gpu() -> Optional[Any]:
    """GPUCollector backend (DCGM or NVML); None when no GPU backend exists."""
    from core.readers.gpu_collector import GPUCollector
    col = GPUCollector(rapl_reader=None)
    return col if col.backend.is_available() else None


def _catalog_sensors(readers: Dict) -> Optional[Callable[[], List[Dict]]]:
    """
    The platform thermal reader's catalog sensors as rows, or None.

    Linux zones are already recorded by THERMAL_ZONE (te_thermal_samples), so
    they are left out here and stored once; on Apple Silicon and for sensors
    such as x86 coretemp this is the only between-runs path.
    """
    from alems_sdk.thermal_catalog import expected_ids, sensor_quality
    try:
        from core.readers.factory import ReaderFactory     # built exactly as _energy() does
        cfg = json.loads(_HW_CONFIG.read_text()) if _HW_CONFIG.exists() else {}
        reader = ReaderFactory.get_thermal_reader(cfg)
        expected = expected_ids(reader.describe_sensors(), reader.get_name())
    except Exception as exc:
        logger.info("recording: no catalog sensors (%s)", exc)
        return None
    if "THERMAL_ZONE" in readers:
        expected = [i for i in expected if not i.startswith("thermal_zone:")]
    if not expected:
        return None
    sha = (cfg.get("thermal_sensors") or {}).get("catalog_sha256")

    def _read() -> List[Dict]:
        vals, qual = sensor_quality(reader.read_sensors() or {}, expected)
        return [{"catalog_id": i, "temp_celsius": vals[i], "quality_flag": qual[i],
                 "catalog_sha256": sha} for i in expected]
    return _read


def _signals_source() -> Optional[Callable[[], List[Dict]]]:
    """Thermal-response signals (platform reader and NVML) as typed rows, or None."""
    from alems_sdk.thermal_catalog import typed_value
    try:
        from core.readers.factory import ReaderFactory
        from core.readers.nvml_signals import NvmlSignals
        cfg = json.loads(_HW_CONFIG.read_text()) if _HW_CONFIG.exists() else {}
        sources = [s for s in (ReaderFactory.get_thermal_reader(cfg), NvmlSignals())
                   if s.describe_signals()]
    except Exception as exc:
        logger.info("recording: no signals (%s)", exc)
        return None
    if not sources:
        return None
    sha = (cfg.get("thermal_sensors") or {}).get("signals_sha256")

    def _read() -> List[Dict]:
        rows = []
        for src in sources:
            for sid, v in (src.read_signals() or {}).items():
                vi, vr, vt = typed_value(v.get("value"))
                rows.append({"signal_id": sid, "value_int": vi, "value_real": vr, "value_text": vt,
                             "quality_flag": v.get("status") or "UNAVAILABLE",
                             "signal_catalog_sha256": sha})
        return rows
    return _read


# readers that return zeros instead of measurements (LIMITED / INFERRED):
# never offered to the recorder, so no zero is ever stored as energy (MIC-1)
_NO_MEASUREMENT_READERS = ("DummyEnergyReader", "EnergyEstimator")


def _energy() -> Optional[Callable[[], List[Dict]]]:
    """
    Platform energy counters as {key, energy_uj} rows, or None.

    Built by the reader factory exactly as for runs. The recording extension
    reads it only inside idle windows, and only when the machine profile's
    main channel is an energy counter.
    """
    try:
        from core.readers.factory import ReaderFactory
        cfg = json.loads(_HW_CONFIG.read_text()) if _HW_CONFIG.exists() else {}
        reader = ReaderFactory.get_energy_reader(cfg)
    except Exception as exc:
        logger.info("recording: no energy reader (%s)", exc)
        return None
    if type(reader).__name__ in _NO_MEASUREMENT_READERS or not reader.is_available():
        return None

    def _read() -> List[Dict]:
        raw = reader.read_energy_uj() or {}
        return [{"key": k, "energy_uj": v,
                 "quality_flag": "VALID" if v is not None else "MISSING"}
                for k, v in raw.items()]
    return _read


def build_readers() -> Tuple[Dict[str, Callable[[], List[Dict]]], Callable[[], None]]:
    """
    Reader callables per source, plus a cleanup function.

    Each callable returns a list of row dicts; the recorder stamps the time.
    """
    readers: Dict[str, Callable[[], List[Dict]]] = {}
    thermal, cooling = _thermal_and_cooling()
    if thermal is not None:
        readers["THERMAL_ZONE"] = thermal.read_all_zones
    if cooling is not None:
        readers["COOLING"] = cooling.read_all_devices
    rails = _power_rails()
    if rails is not None:
        readers["POWER_RAIL"] = rails.read_once
    gpu = _gpu()
    if gpu is not None:
        def _gpu_temp() -> List[Dict]:
            sig = gpu.backend.read_signals(0) or {}
            t = sig.get("temperature_c")
            return [{"gpu_index": 0, "temperature_c": t, "source": gpu.source,
                     "quality_flag": "VALID" if t is not None else "MISSING",
                     "invalid_reason": None if t is not None else "NO_TEMPERATURE_SIGNAL"}]
        readers["GPU_TEMP"] = _gpu_temp

    energy = _energy()
    if energy is not None:
        readers["ENERGY"] = energy
    sensors = _catalog_sensors(readers)
    if sensors is not None:
        readers["SENSORS"] = sensors
    signals = _signals_source()
    if signals is not None:
        readers["SIGNALS"] = signals

    def cleanup() -> None:
        """Release GPU handles (DCGM groups, NVML) opened for the recording."""
        b = getattr(gpu, "backend", None)
        for fn in ("cleanup", "shutdown"):
            if b is not None and hasattr(b, fn):
                try:
                    getattr(b, fn)()
                except Exception as exc:
                    logger.debug("recording cleanup %s: %s", fn, exc)
    logger.info("recording readers: %s", ",".join(sorted(readers)) or "none")
    return readers, cleanup


def rates_from_policy() -> Dict[str, float]:
    """Rates from the sampling policy; power rails keep the engine's 10 Hz."""
    from core.readers.sampling_policy import effective_hz
    rates = {"POWER_RAIL": 10.0}
    for src, key, default in (("THERMAL_ZONE", "THERMAL_ZONE", 20.0),
                              ("COOLING", "COOLING", 1.0),
                              ("AMBIENT", "AMBIENT", 1.0),
                              ("GPU_TEMP", "GPU_COLLECTOR", 10.0)):
        try:
            rates[src] = float(effective_hz(key, default))
        except Exception as exc:            # policy refuses: keep default, say so
            logger.warning("sampling policy has no rate for %s (%s); using %.1f Hz",
                           key, exc, default)
            rates[src] = default
    return rates


def _recorder_class() -> Any:
    """The recorder registered under alems.recorders (first entry)."""
    from importlib.metadata import entry_points
    eps = entry_points()
    group = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") \
        else eps.get(ENTRY_POINT_GROUP, [])
    for ep in group:
        return ep.load()
    raise RuntimeError("no recorder registered under %s (is the extension installed?)"
                       % ENTRY_POINT_GROUP)


def open_recording(pre_window_s: float = 30.0, cooldown_max_s: float = 0.0,
                   machine_profile: str = "unknown", extra_readers: Optional[Dict] = None,
                   recorder_kwargs: Optional[Dict] = None,
                   settings: Optional[Dict] = None) -> Any:
    """
    Open a recording, register it with the run hook and close it at exit.

    Returns the recorder (call close() to end it earlier).
    """
    settings = dict(settings or {})
    readers, cleanup = build_readers()
    readers.update(extra_readers or {})
    # rules and thresholds live in the extension; core only passes settings on
    cooldown = dict(settings.get("cooldown") or {})
    if cooldown_max_s > 0:
        cooldown["max_s"] = cooldown_max_s
    rates = rates_from_policy()
    if "SENSORS" in readers:
        # same requested rate as thermal zones; the achieved rate is checked per recording
        rates["SENSORS"] = float(rates.get("THERMAL_ZONE", 1.0))
    if "SIGNALS" in readers:
        rates["SIGNALS"] = float(rates.get("THERMAL_ZONE", 1.0))
    if "ENERGY" in readers:
        rates["ENERGY"] = float((settings.get("idle") or {}).get("energy_hz", 10.0))
    cls = _recorder_class()
    rec = cls(readers=readers, writer_factory=_writer_factory(),
              rates_hz=rates, machine_profile=machine_profile,
              pre_window_s=pre_window_s, cooldown=cooldown or None, settings=settings,
              **(recorder_kwargs or {}))
    rec.open()
    hook.set_active(rec)

    failed = {"exc": None}
    prev_hook = sys.excepthook

    def _excepthook(etype, value, tb):
        """Remember an unhandled exception so the recording closes as aborted."""
        failed["exc"] = "%s: %s" % (etype.__name__, value)
        prev_hook(etype, value, tb)
    sys.excepthook = _excepthook

    closed = {"done": False}

    def _close() -> None:
        if closed["done"]:                # closed by the run already; atexit is the backstop
            return
        closed["done"] = True
        hook.clear_active(rec)
        try:
            if failed["exc"]:
                rec.close(status="aborted", reason="experiment ended with " + failed["exc"][:200])
            else:
                rec.close()
        finally:
            cleanup()
            sys.excepthook = prev_hook
    rec.close_and_cleanup = _close
    atexit.register(_close)               # normal exit and exceptions; a kill is recovered later
    return rec
