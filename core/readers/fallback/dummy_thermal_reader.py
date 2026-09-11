"""
core/readers/fallback/dummy_thermal_reader.py

Fallback ThermalReader for platforms where no thermal hardware is accessible.
Used on KVM guests, VMs, observation-only platforms (intel_mac, linux_riscv),
and any platform where ReaderFactory cannot locate a real thermal reader.

Satisfies FULL ThermalReaderABC contract including all attributes accessed
by energy_engine.py. No hasattr checks needed anywhere.

PAC-2 compliant: graceful degradation, never crashes caller.
PAC-4 compliant: complete stub — every attribute present, safe default value.

Platforms that land here:
    KVM ARM guests (debian-vm, fedora-vm, alems-vnic) — no hwmon temp sensors
    DGX Spark (Secure Boot) — SPBM unavailable, thermal limited
    intel_mac — observation-only
    linux_riscv — observation-only
    Any future platform without sysfs thermal paths
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DummyThermalReader:
    """
    No-op ThermalReader for platforms without accessible thermal hardware.
    All methods return safe empty values — never raises exceptions.
    Measurement mode: LIMITED — no temperature or throttle data available.

    Class-level attributes satisfy full ThermalReaderABC interface contract.
    energy_engine.py reads these directly — must be present on all instances.
    """

    # --- Interface contract attributes (PAC-4) ---
    available_sensors   = []    # energy_engine gates sampling on this
    throttle_thresholds = {}    # energy_engine reads per-role thresholds

    def __init__(self, config: dict = None):
        """Accept config for interface compatibility. Nothing to initialise."""
        self._config = config or {}
        logger.debug(
            "DummyThermalReader initialised (LIMITED mode). "
            "No thermal hardware accessible on this platform."
        )

    def initialize(self) -> None:
        """No-op. No thermal subsystem to initialise on this platform."""
        logger.debug("DummyThermalReader: initialize called — no-op (LIMITED mode)")

    def is_available(self) -> bool:
        """Always False — no thermal hardware accessible."""
        return False

    def get_name(self) -> str:
        """Identify this reader for logging and metadata."""
        return "DummyThermalReader(LIMITED)"

    def read_temperatures(self) -> Dict[str, Optional[float]]:
        """
        Return empty temperature dict.
        energy_engine.py calls this at run start/end for thermal delta.
        Returning empty dict means thermal_delta_c = None in runs table.
        """
        return {}

    def read_all_thermal(self) -> Dict:
        """
        Return empty thermal reading dict.
        energy_engine.py calls this in the thermal sampling loop.
        Returning empty dict means no thermal_samples rows written.
        """
        return {}

    def read_all_zones(self) -> List[Dict]:
        """
        Return empty zone list.
        ThermalReaderV2ABC contract — called by some collectors.
        """
        return []

    def read_all_devices(self) -> List[Dict]:
        """
        Return empty device list.
        ThermalReaderV2ABC contract — called by some collectors.
        """
        return []

    def get_throttle_status(self) -> Dict[str, bool]:
        """
        Return empty throttle status dict.
        No throttle detection possible without thermal hardware.
        """
        return {}

    def start_monitoring(self, interval_ms: int = 1000) -> None:
        """No-op — no thermal process to start on this platform."""
        logger.debug(
            "DummyThermalReader: start_monitoring called — no-op (LIMITED mode)"
        )

    def stop_monitoring(self) -> Dict:
        """Return empty result dict — no thermal data collected."""
        return {
            "samples":          [],
            "num_samples":      0,
            "duration_seconds": 0.0,
        }
