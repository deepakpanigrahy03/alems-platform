"""
================================================================================
THERMAL EXPOSURE: ROOM TEMPERATURE (AMBIENT) READER INTERFACE
================================================================================

PURPOSE:
    Interface for one external room temperature probe. No probe is fitted
    yet (probe and driver are chosen after a hardware check), so this reader
    reports itself unavailable on every platform and every read returns no
    value with status UNAVAILABLE. Sessions then store ambient as NULL plus
    that status in te_ambient_samples, never as zero (MIC-1).

ADDING A PROBE LATER:
    Subclass AmbientReader, implement _read_probe_celsius() and
    is_available(), and register the subclass through an entry point.
    Raise the AMBIENT bound in alems_sdk.measurement_schema only with
    evidence in config/methodology_refs/.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
import time
from typing import Any, Dict, Optional

from alems_sdk.readers import BaseReader

logger = logging.getLogger(__name__)

# Validity range shared with the zone reader (ThermalReaderV2) for comparability
VALID_MIN_C = -10.0
VALID_MAX_C = 125.0
SENSOR_NAME = "ambient"


class AmbientReader(BaseReader):
    """External room temperature probe; unavailable until a probe is fitted."""

    # Thermal registry contract (core/readers/registry.py): can_handle() is False
    # until a probe exists, so this class is never selected over the zone reader
    # and can never tie with it. PRIORITY is high (lower wins) for the same reason.
    METHOD_ID = "ambient_probe_v1"
    FIDELITY = "LIMITED"        # no probe fitted: hardware unavailable (PAC-3)
    PRIORITY = 900

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """No configuration keys until a probe and its driver are chosen."""
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @classmethod
    def can_handle(cls, caps) -> bool:
        """Never eligible as the machine thermal reader (room probe only)."""
        return False

    def get_name(self) -> str:
        """Reader identity used in logs and registration."""
        return "ambient_probe"

    def is_available(self) -> bool:
        """False on every platform until a probe and driver exist (never raises)."""
        return False

    def _read_probe_celsius(self) -> Optional[float]:
        """Raw probe value in Celsius; None here because no probe is fitted."""
        return None

    def read(self) -> Dict:
        """
        One room temperature reading in the te_ambient_samples layout.

        Returns:
            Dict with sensor_name, timestamp_ns, temp_celsius (None when no
            value), quality_flag and invalid_reason. Never raises.
        """
        ts = time.time_ns()
        if not self.is_available():
            return _row(ts, None, "UNAVAILABLE", "NO_PROBE_FITTED")
        try:
            temp = self._read_probe_celsius()
        except Exception as exc:          # a probe failure must not stop a session
            logger.warning("ambient probe read failed: %s", exc)
            return _row(ts, None, "READ_FAILED", str(exc)[:200])
        if temp is None:
            return _row(ts, None, "MISSING", "PROBE_RETURNED_NONE")
        if temp < VALID_MIN_C or temp > VALID_MAX_C:
            return _row(ts, temp, "OUT_OF_RANGE", "TEMP=%.1fC" % temp)
        return _row(ts, temp, "VALID", None)


def _row(ts: int, temp: Optional[float], flag: str, reason: Optional[str]) -> Dict:
    """Build one reading dict (keeps the four quality words plus UNAVAILABLE)."""
    return {"sensor_name": SENSOR_NAME, "timestamp_ns": ts, "temp_celsius": temp,
            "quality_flag": flag, "invalid_reason": reason}
