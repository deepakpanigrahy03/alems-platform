"""
ARM thermal reader via /sys/class/thermal/ sysfs for GN100 (Grace + Blackwell).
Implements ThermalReaderABC as a drop-in replacement for SensorReader on aarch64.

On GN100 all 7 thermal zones are type=acpitz (ACPI thermal zone), reporting
millidegrees Celsius. SensorReader is skipped because it reads from
hw_config["thermal"]["paths"] which are x86-specific hardcoded paths.
This reader discovers zones dynamically — no hw_config entries needed.

Source: /sys/class/thermal/thermal_zone*/temp  (millidegrees Celsius)
Method: arm_thermal_sysfs_v1
Confidence: 0.90

cp to: core/readers/arm_thermal_reader.py
"""

import glob
import logging
from typing import Dict, List, Optional

from core.readers.interfaces import ThermalReaderABC

logger = logging.getLogger(__name__)

# Zone type substrings that indicate a CPU package / SoC temperature zone.
# acpitz is what GN100 Grace reports; grace/neoverse/soc added for future ARM variants.
CPU_ZONE_KEYWORDS = ["acpitz", "cpu", "package", "soc", "grace", "neoverse"]


class ARMThermalReader(ThermalReaderABC):
    """
    Reads thermal zones from /sys/class/thermal/ sysfs on ARM Linux.

    Discovers zones dynamically at construction — never relies on
    hw_config paths which are x86-specific. Averages all CPU-type
    zones to produce package_temp_celsius (matches SensorReader contract).

    Used on GN100 (aarch64) where SensorReader returns {} because
    its hw_config thermal paths are x86 MSR/hwmon paths not present on Grace.
    """
     
    METHOD_ID: str = "arm_thermal_sysfs"
    FIDELITY = "MEASURED"
    PRIORITY: int  = 100
 
    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible on Linux aarch64 with thermal zones available."""
        return (
            caps.os == "Linux"
            and caps.arch == "aarch64"
            and caps.has_thermal
        )


    def __init__(self, config: dict):
        # config kept for interface consistency; not used (no hw_config paths needed)
        self._config = config
        # {zone_type: temp_path} for all readable zones
        self._zones = self._discover_zones()
        # subset of zone keys identified as CPU/SoC temperature
        self._cpu_zone_keys = self._find_cpu_zones()
        logger.info(
            "ARMThermalReader: discovered %d zones, %d cpu zones: %s",
            len(self._zones),
            len(self._cpu_zone_keys),
            self._cpu_zone_keys,
        )

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------

    def _discover_zones(self):
        # type: () -> Dict[str, str]
        """
        Walk /sys/class/thermal/thermal_zone* and return readable zones.

        Returns:
            Dict mapping zone_type string to temp sysfs path.
            Duplicate types get a numeric suffix (acpitz_0, acpitz_1, ...).
        """
        zones = {}
        type_counts = {}  # track how many times each type appears

        for zone_dir in sorted(glob.glob("/sys/class/thermal/thermal_zone*/")):
            try:
                zone_type = open(zone_dir + "type").read().strip()
                temp_path = zone_dir + "temp"
                # verify temp file is readable before registering
                open(temp_path).read()
            except Exception as exc:
                logger.debug("ARMThermalReader: skipping %s: %s", zone_dir, exc)
                continue

            # de-duplicate type names with a counter suffix
            count = type_counts.get(zone_type, 0)
            key = zone_type if count == 0 else f"{zone_type}_{count}"
            type_counts[zone_type] = count + 1
            zones[key] = temp_path

        return zones

    def _find_cpu_zones(self):
        # type: () -> List[str]
        """
        Return the subset of zone keys that represent CPU/SoC temperature.

        Matches against CPU_ZONE_KEYWORDS. On GN100 all zones are acpitz
        so all 7 are selected and averaged — this gives a stable package
        temperature robust to per-core variation.
        """
        cpu_keys = [
            key for key in self._zones
            if any(kw in key.lower() for kw in CPU_ZONE_KEYWORDS)
        ]
        # fallback: if no keyword matched, use all zones rather than return nothing
        if not cpu_keys and self._zones:
            cpu_keys = list(self._zones.keys())
            logger.warning(
                "ARMThermalReader: no CPU zone keywords matched; using all %d zones",
                len(cpu_keys),
            )
        return cpu_keys

    # ------------------------------------------------------------------
    # Low-level read
    # ------------------------------------------------------------------

    def _read_millidegrees(self, path):
        # type: (str) -> Optional[int]
        """
        Read raw millidegree value from sysfs path.

        Returns:
            Integer millidegrees, or None on any read failure.
        """
        try:
            return int(open(path).read().strip())
        except Exception as exc:
            logger.debug("ARMThermalReader: read failed path=%s: %s", path, exc)
            return None

    def _read_celsius(self, path):
        # type: (str) -> Optional[float]
        """
        Read sysfs temp path and convert millidegrees to Celsius.

        Returns:
            Temperature in degrees Celsius, or None on failure.
        """
        raw = self._read_millidegrees(path)
        if raw is None:
            return None
        # sysfs always reports in millidegrees — divide by 1000
        return raw / 1000.0

    # ------------------------------------------------------------------
    # ThermalReaderABC interface
    # ------------------------------------------------------------------

    def initialize(self):
        # type: () -> None
        """
        No-op initialize for interface compatibility with SensorReader.
        ARMThermalReader discovers zones at __init__ time — no deferred
        initialization needed. Called by energy_engine.py after construction.
        """
        # available_sensors mirrors SensorReader interface — energy_engine.py
        # checks len(self.sensor.available_sensors) and bool(self.sensor.available_sensors)
        self.available_sensors = self._zones.copy()
        # throttle_thresholds mirrors SensorReader interface — energy_engine.py
        # calls self.sensor.throttle_thresholds.get(role); empty dict = no throttling
        self.throttle_thresholds = {}
        logger.debug("ARMThermalReader.initialize(): %d zones ready", len(self._zones))

    def is_available(self):
        # type: () -> bool
        """Return True if at least one thermal zone was discovered."""
        return len(self._zones) > 0

    def get_name(self):
        # type: () -> str
        """Reader identifier for logging and provenance."""
        return "ARMThermalReader"

    def read_all_thermal(self):
        # type: () -> Dict[str, Optional[float]]
        """
        Read all discovered thermal zones plus the standard harness keys.

        Returns a dict with every zone key in Celsius, plus:
          - "cpu_temp": average of all CPU-type zones (consumed by harness)
          - "package_celsius": same as cpu_temp (consumed by read_temperatures)

        Matches the interface contract that harness.py expects from SensorReader.
        """
        result = {}

        # read every discovered zone
        for key, path in self._zones.items():
            result[key] = self._read_celsius(path)

        # compute cpu_temp average across all CPU-identified zones
        cpu_readings = [
            result[k] for k in self._cpu_zone_keys if result.get(k) is not None
        ]
        if cpu_readings:
            cpu_avg = sum(cpu_readings) / len(cpu_readings)
        else:
            # MIC-1: return None not 0.0 when genuinely unavailable
            cpu_avg = None

        # standard keys consumed by the harness thermal pipeline
        result["cpu_temp"] = cpu_avg
        result["package_celsius"] = cpu_avg

        logger.debug(
            "ARMThermalReader: cpu_avg=%.1f°C from %d zones",
            cpu_avg or 0.0,
            len(cpu_readings),
        )
        return result

    # ── Sensor catalog (SDK describe_sensors / read_sensors) ──────────────

    def _key_ids(self):
        # type: () -> Dict[str, str]
        """Reader key -> catalog id; zone type and index come from sysfs, not the key."""
        import os
        from core.thermal.thermal_descriptors import zone_id
        out = {}
        for key, path in self._zones.items():
            zdir = os.path.dirname(path)
            with open(os.path.join(zdir, "type")) as fh:
                out[key] = zone_id(int(os.path.basename(zdir)[len("thermal_zone"):]), fh.read().strip())
        return out

    def describe_sensors(self):
        # type: () -> List[Dict]
        """Each zone as a sensor; cpu_temp and package_celsius as derived means."""
        import os
        from alems_sdk.thermal_catalog import descriptor
        from core.thermal.thermal_descriptors import list_zones, zone_descriptors
        dirs = {os.path.dirname(p) for p in self._zones.values()}
        zones = [z for z in list_zones() if z["dir"].rstrip("/") in {d.rstrip("/") for d in dirs}]
        ids = self._key_ids()
        cpu = [ids[k] for k in self._cpu_zone_keys if k in ids]
        derived = [descriptor("%s:%s" % (self.get_name(), k), "derived", "derived", "celsius_float",
                              "%s:%s" % (self.get_name(), k), "strong", self.get_name(),
                              derived_from=cpu, formula="mean of VALID inputs", scope="zone")
                   for k in ("cpu_temp", "package_celsius")]
        return zone_descriptors(self.get_name(), zones) + derived + self._board_descriptors()

    def read_sensors(self):
        # type: () -> Dict[str, Optional[float]]
        """Zone temperatures under their catalog ids, plus the derived means."""
        values, ids = self.read_all_thermal(), self._key_ids()
        out = {ids[k]: v for k, v in values.items() if k in ids}
        out.update({"%s:%s" % (self.get_name(), k): values.get(k) for k in ("cpu_temp", "package_celsius")})
        out.update(self._board_values())
        return out

    def _board_inputs(self):
        # type: () -> List[Dict]
        """Named board sensors (spbm hwmon: CPU clusters, GPU, SoC); cached, [] elsewhere."""
        if getattr(self, "_board", None) is None:
            from core.thermal.thermal_descriptors import hwmon_inputs
            self._board = [h for h in hwmon_inputs() if h["name"] == "spbm"]
        return self._board

    def _board_descriptors(self):
        # type: () -> List[Dict]
        """Catalog descriptors of the board sensors, recorded by this reader (converted values)."""
        from core.thermal.thermal_descriptors import hwmon_descriptors
        ids = {h["id"] for h in self._board_inputs()}
        out = []
        for d in hwmon_descriptors(self.get_name()):
            if d["id"] in ids:
                d.update(reported_format="decikelvin_shown_as_millidegree",
                         interface_resolution_c=0.1,
                         formula="C = hwmon_C * 10 - 273.2 (spbm_temp_decikelvin_v1)",
                         notes="firmware reports 0.1 K; the spark_hwmon driver presents it as C; "
                               "converted here; same sensor as the matching ACPI zone; a value "
                               "below 1 C is an empty slot and is stored as missing")
                out.append(d)
        return out

    # firmware tenths of a kelvin, shown by the driver as if Celsius (method spbm_temp_decikelvin_v1)
    _SPBM_K_OFFSET = 273.2
    _SPBM_MIN_C = 1.0          # below this on a powered chip: empty slot, not a reading

    def _board_values(self):
        # type: () -> Dict[str, Optional[float]]
        """Board sensor temperatures in C, converted; a failed read or empty slot is None, never 0."""
        out = {}
        for h in self._board_inputs():
            try:
                with open(h["path"]) as fh:
                    shown_c = int(fh.read().strip()) / 1000.0
            except (OSError, ValueError):
                out[h["id"]] = None
                continue
            c = round(shown_c * 10.0 - self._SPBM_K_OFFSET, 2)
            out[h["id"]] = c if c >= self._SPBM_MIN_C else None
        return out

    def read_temperatures(self):
        # type: () -> Dict
        """
        Return ThermalReadings-compatible dict for callers expecting that shape.

        Harness uses read_all_thermal(); this method satisfies the ABC contract
        and any caller that checks read_temperatures() directly.
        """
        thermal = self.read_all_thermal()
        cpu_temp = thermal.get("cpu_temp")
        return {
            # package_celsius is the key consumed by energy_analyzer
            "package_celsius":  cpu_temp if cpu_temp is not None else 0.0,
            "core_temps":       [],
            "gpu_celsius":      0.0,   # GPU temp comes from DCGM, not sysfs
            "pch_celsius":      0.0,
            "throttle_events":  0,
            "prochot_events":   0,
            "is_throttling":    False,
        }
