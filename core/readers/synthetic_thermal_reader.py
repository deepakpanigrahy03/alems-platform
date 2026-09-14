#!/usr/bin/env python3
"""
================================================================================
SYNTHETIC THERMAL READER  —  core/readers/synthetic_thermal_reader.py
================================================================================

Purpose:
    A deterministic thermal reader that returns fixed temperature values
    instead of reading hardware thermal sensors.
    Used exclusively when ALEMS_PLATFORM_OVERRIDE=synthetic is set.

Fixed values returned:
    cpu_package: 45.0°C  (realistic idle-to-light-load temperature)

45°C is chosen as a realistic operating temperature that:
    - Is above ambient (confirms the reader is returning data, not zeros)
    - Is below thermal throttle threshold (85°C typical)
    - Is the same value every call (no noise, easy to assert in tests)

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part B
================================================================================
"""

import logging
from typing import Dict, List, Optional

from core.readers.interfaces import ThermalReaderABC

logger = logging.getLogger(__name__)


class SyntheticThermalReader(ThermalReaderABC):
    """
    Deterministic thermal reader for synthetic platform testing.

    Returns fixed temperature values.
    Selected only when caps.platform_class == "synthetic".
    """

    # ------------------------------------------------------------------
    # Registry contract (SPEC 35A)
    # ------------------------------------------------------------------
    METHOD_ID: str = "synthetic_thermal"
    PRIORITY: int  = 100

    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible ONLY when platform_class is 'synthetic'."""
        return getattr(caps, "platform_class", "") == "synthetic"

    # Fixed temperature value — realistic, above ambient, below throttle
    _CPU_PACKAGE_CELSIUS: float = 45.0

    def __init__(self, config: dict = None):
        self._config = config or {}
        logger.info(
            "SyntheticThermalReader: initialized (cpu_package=%.1f°C)",
            self._CPU_PACKAGE_CELSIUS,
        )

    # ------------------------------------------------------------------
    # ThermalReaderABC interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Always True on synthetic platform."""
        return True

    def get_name(self) -> str:
        """Return reader name for logging."""
        return "SyntheticThermalReader"

    def read_all_thermal(self) -> Dict[str, float]:
        """
        Return fixed temperature readings for all thermal zones.

        Returns:
            Dict mapping zone name to temperature in Celsius.
            {'cpu_package': 45.0}
        """
        return {"cpu_package": self._CPU_PACKAGE_CELSIUS}
