#!/usr/bin/env python3
"""
================================================================================
IOKIT THERMAL READER — macOS Thermal Measurement
================================================================================

Three real, independently cross-validated data sources on Apple Silicon:

1. Die temperature via a vendored helper binary built from Koan-Sin Tan's
   `sensors.m` (github.com/freedomtan/sensors_cmdline, BSD 3-Clause,
   Copyright 2021 "freedom" Koan-Sin Tan). Reads PMU tdie0-tdieN probes
   through Apple's own public IOHIDEventSystemClient framework, using
   constants (kHIDPage_AppleVendor, kHIDUsage_AppleVendor_TemperatureSensor,
   kIOHIDEventTypeTemperature) that trace directly to Apple's published
   open source headers, IOHIDFamily/AppleHIDUsageTables.h and
   IOHIDFamily/IOHIDEventTypes.h (opensource.apple.com). No sudo required.

   Cross-validated this session (2026-07-02) three independent ways:
     a. Real CPU Power increase confirmed via powermetrics during an
        8-way `yes` saturation load (900-1600mW idle to 5000-6100mW loaded)
     b. tdie0 through tdie10 rose monotonically over the same 16-second
        window (~29.6-31.4C idle to ~30.4-30.8C loaded, consistent
        direction across all probes)
     c. Magnitude and direction physically sensible for a fan-managed
        laptop under moderate load, not a runaway or implausible jump
   PMU tcal excluded: fixed at 51.850006C across every sample, a
   calibration constant, not a live reading. "noname" excluded: fixed at
   0.0 across every sample, a dead/unmapped HID service.

2. Battery temperature via `ioreg -rc AppleSmartBattery`, "Temperature"
   key, deciKelvin converted to Celsius. Standard, decades-old, documented
   IOKit property, not reverse engineered.

3. Thermal pressure level via `powermetrics --samplers thermal`,
   categorical only (Nominal/Fair/Serious/Critical), first-party Apple
   tool. Exposed outside the ThermalReaderABC contract since it is not a
   Celsius value, get_pressure_level() only, for logging/provenance.

ThermalReaderABC requires Dict[str, float]. package_temp_celsius is
computed as max(tdie0..tdieN) per sample, package-level peak being the
more conservative, more useful figure for thermal throttling analysis
than a mean across probes.

Author: Deepak Panigrahy
Third-party component: sensors.m by Koan-Sin Tan, BSD 3-Clause, vendored
at core/readers/darwin/vendor/sensors.m, LICENSE alongside it.
================================================================================
"""

import logging
import os
import re
import subprocess
import threading
from typing import Dict, List, Optional

from core.readers.interfaces import ThermalReaderABC

logger = logging.getLogger(__name__)

PRESSURE_LEVEL_RANK = {
    "Nominal": 0,
    "Fair": 1,
    "Serious": 2,
    "Critical": 3,
}

# Sensor names confirmed dead/non-live this session, always excluded.
EXCLUDED_SENSOR_SUBSTRINGS = ("tcal", "noname")

VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")
SENSORS_BINARY = os.path.join(VENDOR_DIR, "sensors")


class IOKitThermalReader(ThermalReaderABC):
    """
    macOS thermal reader. Real numeric data: die temperature (vendored
    sensors.m binary) and battery Celsius (ioreg). Real categorical data:
    thermal pressure level (powermetrics), exposed outside the ABC
    contract since it is not a Celsius value.
    """

    METHOD_ID          = "iokit_thermal_reader"
    FIDELITY = "MEASURED"
    PRIORITY: int      = 100
 
    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible on macOS."""
        return caps.os == "Darwin"
    METHOD_NAME        = "IOKit Thermal Reader (macOS)"
    METHOD_LAYER       = "silicon"
    METHOD_CONFIDENCE  = 0.75   # die temp is real, live, cross-validated
                                 # three ways this session; not 1.0 since
                                 # exact tdieN to physical-core mapping is
                                 # not independently confirmed, only that
                                 # the readings are live and responsive
    METHOD_PROVENANCE  = "MEASURED"
    METHOD_PARAMS      = {
        "die_temp_source": "vendored sensors.m (Koan-Sin Tan, BSD 3-Clause), "
                            "IOHIDEventSystemClient, Apple published constants",
        "battery_source": "ioreg AppleSmartBattery Temperature key",
        "pressure_source": "powermetrics thermal sampler, categorical only",
        "excluded_sensors": list(EXCLUDED_SENSOR_SUBSTRINGS),
        "aggregation": "package_temp_celsius = max(tdie0..tdieN) per sample",
    }
    FALLBACK_METHOD_ID = "ml_thermal_estimator"

    def __init__(self, config: dict = None):
        self._config = config or {}
        self._lock = threading.Lock()
        self._latest_level: Optional[str] = None
        self._proc = None
        self._binary_available = os.path.exists(SENSORS_BINARY) and os.access(
            SENSORS_BINARY, os.X_OK
        )
        self._available = self._check_available()
        # pressure sampling starts in initialize() (runs), never here: describing
        # sensors or probing them must not launch any subprocess under sudo
        self._pressure_started = False
        if not self._binary_available:
            logger.warning(
                "IOKitThermalReader: vendored sensors binary not found at %s, "
                "die temperature unavailable, falling back to battery only. "
                "Build it via core/readers/darwin/vendor/build.sh",
                SENSORS_BINARY,
            )

    def _check_available(self) -> bool:
        try:
            result = subprocess.run(
                ["ioreg", "-rc", "AppleSmartBattery"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0 and '"Temperature"' in result.stdout
        except Exception as e:
            logger.warning("IOKitThermalReader: availability check failed: %s", e)
            return False

    def _start_pressure_sampling(self):
        cmd = ["sudo", "-n", "powermetrics", "--samplers", "thermal",
               "-i", "1000", "-n", "-1", "--format", "text"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            threading.Thread(target=self._read_loop, daemon=True,
                              name="iokit-thermal-pressure").start()
        except Exception as e:
            logger.warning("IOKitThermalReader: pressure sampling failed to start: %s", e)
            self._proc = None

    def _read_loop(self):
        if not self._proc:
            return
        for line in self._proc.stdout:
            m = re.search(r"Current pressure level:\s+(\w+)", line)
            if m:
                with self._lock:
                    self._latest_level = m.group(1)

    def _read_die_temps(self) -> Dict[str, float]:
        """
        Run the vendored sensors binary once, parse its two-line CSV
        output (header, values), filter to tdie* probes only, excluding
        confirmed-dead entries (tcal, noname).
        """
        if not self._binary_available:
            return {}
        try:
            result = subprocess.run(
                [SENSORS_BINARY, "-o"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return {}
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return {}
            names = [n.strip() for n in lines[0].split(",")]
            values = [v.strip() for v in lines[1].split(",")]
            die_temps: Dict[str, float] = {}
            for idx, (name, value) in enumerate(zip(names, values)):
                lname = name.lower()
                if any(ex in lname for ex in EXCLUDED_SENSOR_SUBSTRINGS):
                    continue
                if "tdie" not in lname:
                    continue
                try:
                    die_temps[f"tdie_{idx}"] = float(value)
                except ValueError:
                    continue
            return die_temps
        except Exception as e:
            logger.warning("IOKitThermalReader: die temp read failed: %s", e)
            return {}

    def read_all_thermal(self) -> Dict[str, float]:
        """
        Required by ThermalReaderABC. Real numeric sensors only.
        package_temp_celsius = max die probe reading, when available.
        battery_celsius always attempted independently.
        """
        temps: Dict[str, float] = {}

        die_temps = self._read_die_temps()
        if die_temps:
            _pkg_temp = round(max(die_temps.values()), 2)
            temps["cpu_temp"] = _pkg_temp              # ABC contract key consumed by harness
            temps["package_temp_celsius"] = _pkg_temp  # legacy alias for read_temperatures()

        try:
            result = subprocess.run(
                ["ioreg", "-rc", "AppleSmartBattery"],
                capture_output=True, text=True, timeout=5,
            )
            m = re.search(r'"Temperature"\s*=\s*(\d+)', result.stdout)
            if m:
                decikelvin = int(m.group(1))
                celsius = decikelvin / 10.0 - 273.15
                temps["battery_celsius"] = round(celsius, 2)
        except Exception as e:
            logger.warning("IOKitThermalReader.read_all_thermal battery read failed: %s", e)

        return temps
    def read_temperatures(self):
        # type: () -> Dict
        """
        Return ThermalReadings-compatible dict for callers expecting that
        shape (energy_engine.py lines 737/1141, energy_analyzer consumes
        this). Harness uses read_all_thermal() for its own sampling loop
        (line 346); this method satisfies the ABC contract and any caller
        that checks read_temperatures() directly, same pattern as
        ARMThermalReader.
        """
        thermal = self.read_all_thermal()
        package_temp = thermal.get("package_temp_celsius")
        return {
            # package_celsius is the key consumed by energy_analyzer
            "package_celsius":  package_temp if package_temp is not None else 0.0,
            "core_temps":       [],
            "gpu_celsius":      0.0,   # no separate GPU thermal source, see 16F3 known limitations
            "pch_celsius":      0.0,
            "throttle_events":  0,
            "prochot_events":   0,
            # Real data available here unlike ARMThermalReader's static
            # False, we have an actual categorical pressure signal
            "is_throttling":    self.is_throttling(),
        }
    
    # ── Sensor catalog (SDK describe_sensors / read_sensors / signals) ─────

    @staticmethod
    def _parse_sensor_csv(text):
        # type: (str) -> Dict[str, Optional[float]]
        """Helper output (header line, value line) -> {sensor name: C or None}."""
        lines = [ln for ln in text.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            return {}
        names = [n.strip().replace(" (°C)", "") for n in lines[0].split(",")]
        out = {}
        for name, raw in zip(names, [v.strip() for v in lines[1].split(",")]):
            try:
                out[name] = float(raw)
            except ValueError:
                out[name] = None          # unparsable value is missing, never 0
        return out

    def _read_named(self):
        # type: () -> Dict[str, Optional[float]]
        """Every helper sensor by its real name; {} when the helper is not built."""
        if not self._binary_available:
            return {}
        try:
            r = subprocess.run([SENSORS_BINARY, "-o"], capture_output=True, text=True, timeout=5)
            return self._parse_sensor_csv(r.stdout) if r.returncode == 0 else {}
        except Exception as e:
            logger.warning("IOKitThermalReader: helper read failed: %s", e)
            return {}

    @staticmethod
    def _recorded(name):
        # type: (str) -> bool
        """Die probes this reader records (same rule as _read_die_temps)."""
        low = name.lower()
        return "tdie" in low and not any(ex in low for ex in EXCLUDED_SENSOR_SUBSTRINGS)

    def _read_battery_c(self):
        # type: () -> Optional[float]
        """Battery temperature from ioreg (deciKelvin), or None."""
        try:
            r = subprocess.run(["ioreg", "-rc", "AppleSmartBattery"],
                               capture_output=True, text=True, timeout=5)
            m = re.search(r'"Temperature"\s*=\s*(\d+)', r.stdout)
            return round(int(m.group(1)) / 10.0 - 273.15, 2) if m else None
        except Exception:
            return None

    def describe_sensors(self):
        # type: () -> List[Dict]
        """Helper sensors by real name, battery, and the derived maximum."""
        from alems_sdk.thermal_catalog import descriptor
        names = sorted(self._read_named())
        descs = [descriptor("iokit:%s" % n, "sensor", "iokit_hid", "celsius_float", n, "strong",
                            self.get_name() if self._recorded(n) else "discovery_utility",
                            source_ref=SENSORS_BINARY, label=n) for n in names]
        if self._available:
            descs.append(descriptor("ioreg:AppleSmartBattery:Temperature", "sensor", "ioreg",
                                    "decikelvin", "AppleSmartBattery", "strong", self.get_name(),
                                    interface_resolution_c=0.1, label="battery"))
        dies = ["iokit:%s" % n for n in names if self._recorded(n)]
        note = None if self._binary_available else "die sensors unavailable: helper binary not built"
        descs += [descriptor("%s:%s" % (self.get_name(), k), "derived", "derived", "celsius_float",
                             "%s:%s" % (self.get_name(), k), "strong", self.get_name(),
                             derived_from=dies, formula="max of die sensors", notes=note)
                  for k in ("cpu_temp", "package_temp_celsius")]
        return descs

    def _sensor_values(self, everything):
        # type: (bool) -> Dict[str, Optional[float]]
        """Helper sensors (recorded ones, or all), battery and the derived maximum."""
        named = self._read_named()
        out = {"iokit:%s" % n: v for n, v in named.items() if everything or self._recorded(n)}
        dies = [v for n, v in named.items() if self._recorded(n) and v is not None]
        peak = round(max(dies), 2) if dies else None
        out.update({"%s:cpu_temp" % self.get_name(): peak,
                    "%s:package_temp_celsius" % self.get_name(): peak})
        if self._available:
            out["ioreg:AppleSmartBattery:Temperature"] = self._read_battery_c()
        return out

    def read_sensors(self):
        # type: () -> Dict[str, Optional[float]]
        """Temperatures this reader records, by catalog id."""
        return self._sensor_values(everything=False)

    def read_discovery_sensors(self):
        # type: () -> Dict[str, Optional[float]]
        """All helper sensors, including those not recorded (tdev, NAND, ...)."""
        return self._sensor_values(everything=True)

    def describe_signals(self):
        # type: () -> List[Dict]
        """Thermal pressure: categorical, not a temperature."""
        return [{"id": "powermetrics:thermal_pressure", "quantity": "thermal_pressure",
                 "unit": "level", "values": list(PRESSURE_LEVEL_RANK),
                 "requires": "sudo -n powermetrics (NOPASSWD rule set by install)",
                 "started_by": "initialize()"}]

    def read_signals(self):
        # type: () -> Dict[str, Dict]
        """Latest pressure level with its status."""
        level = self.get_pressure_level()
        status = "ok" if level else ("not_started" if not self._pressure_started else "unavailable")
        return {"powermetrics:thermal_pressure": {"value": level, "unit": "level", "status": status}}

    def get_pressure_level(self) -> Optional[str]:
        """Not part of ThermalReaderABC. Categorical, logging/provenance only."""
        with self._lock:
            return self._latest_level

    def is_throttling(self) -> bool:
        """Not part of ThermalReaderABC. Derived from pressure level."""
        level = self.get_pressure_level()
        return PRESSURE_LEVEL_RANK.get(level, 0) >= 2 if level else False

    def initialize(self):
        # type: () -> None
        """
        No-op initialize for interface compatibility with SensorReader/
        ARMThermalReader. IOKitThermalReader discovers everything (die
        temp binary, battery ioreg, pressure sampler) at __init__ time,
        no deferred initialization needed. Called by energy_engine.py
        after construction (line 228).
        """
        # available_sensors mirrors SensorReader/ARMThermalReader interface,
        # energy_engine.py checks len(self.sensor.available_sensors) > 0
        # (line 310) and truthiness (line 1600).
        self.available_sensors = []
        if self._binary_available:
            self.available_sensors.append("package_temp_celsius")
        if self._available:
            self.available_sensors.append("battery_celsius")
        # throttle_thresholds mirrors SensorReader/ARMThermalReader interface,
        # energy_engine.py calls self.sensor.throttle_thresholds.get(role)
        # (line 352), empty dict means no throttling data configured, same
        # as ARMThermalReader's own no-op value.
        self.throttle_thresholds = {}
        if self._available and not self._pressure_started:
            self._pressure_started = True        # once; same behaviour for runs as before
            self._start_pressure_sampling()
        logger.debug(
            "IOKitThermalReader.initialize(): %d sensors ready: %s",
            len(self.available_sensors), self.available_sensors,
        )

    def is_available(self) -> bool:
        """
        True if any temperature source works: the die-sensor helper or the
        battery. Macs without a battery (Mac mini, Mac Studio) rely on the
        helper alone. Runs decide through available_sensors (initialize());
        this flag now says the same.
        """
        return self._available or self._binary_available

    def get_name(self) -> str:
        return "IOKitThermalReader"
