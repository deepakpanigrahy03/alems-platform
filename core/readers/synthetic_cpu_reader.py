#!/usr/bin/env python3
"""
================================================================================
SYNTHETIC CPU READER  —  core/readers/synthetic_cpu_reader.py
================================================================================

Purpose:
    A deterministic CPU performance counter reader that returns fixed values
    instead of reading hardware PMU counters.
    Used exclusively when ALEMS_PLATFORM_OVERRIDE=synthetic is set.

Fixed values returned:
    instructions:    1,000,000  (1M instructions per measurement window)
    cycles:            500,000  (500K cycles — implies IPC of 2.0)
    ipc:                   2.0  (instructions per cycle)
    frequency_mhz:      2400.0  (2.4 GHz nominal)

These values are chosen to be realistic (2.0 IPC is achievable on modern
ARM and x86) and round (easy to assert in tests).

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part B
================================================================================
"""

import logging
from typing import Any, Dict, Optional

from core.readers.interfaces import CPUReaderABC

logger = logging.getLogger(__name__)


class SyntheticCPUReader(CPUReaderABC):
    """
    Deterministic CPU reader for synthetic platform testing.

    Returns fixed, realistic CPU counter values.
    Selected only when caps.platform_class == "synthetic".
    """

    # ------------------------------------------------------------------
    # Registry contract (SPEC 35A)
    # ------------------------------------------------------------------
    METHOD_ID: str = "synthetic_cpu"
    PRIORITY: int  = 100

    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible ONLY when platform_class is 'synthetic'."""
        return getattr(caps, "platform_class", "") == "synthetic"

    # Fixed values — intentionally round for CI assertions
    _INSTRUCTIONS:    int   = 1_000_000
    _CYCLES:          int   =   500_000
    _IPC:             float =       2.0
    _FREQUENCY_MHZ:   float =    2400.0

    def __init__(self, config: dict = None):
        self._config = config or {}
        logger.info(
            "SyntheticCPUReader: initialized "
            "(instructions=%d cycles=%d ipc=%.1f freq_mhz=%.1f)",
            self._INSTRUCTIONS, self._CYCLES,
            self._IPC, self._FREQUENCY_MHZ,
        )

    # ------------------------------------------------------------------
    # CPUReaderABC interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Always True on synthetic platform."""
        return True

    def get_name(self) -> str:
        """Return reader name for logging."""
        return "SyntheticCPUReader"

    def read_instructions(self) -> int:
        """
        Return fixed instruction count.

        Returns:
            int: 1,000,000 instructions per call.
        """
        return self._INSTRUCTIONS

    def read_cycles(self) -> int:
        """
        Return fixed cycle count.

        Returns:
            int: 500,000 cycles per call.
        """
        return self._CYCLES

    def read_ipc(self) -> float:
        """
        Return fixed IPC (instructions per cycle).

        Returns:
            float: 2.0 IPC.
        """
        return self._IPC

    def read_frequency_mhz(self) -> float:
        """
        Return fixed CPU frequency.

        Returns:
            float: 2400.0 MHz (2.4 GHz).
        """
        return self._FREQUENCY_MHZ

    def start_process_measurement(self, pid: int = 0) -> None:
        """No-op — synthetic reader has no process-level counters."""
        logger.debug("SyntheticCPUReader: start_process_measurement(pid=%d) — no-op", pid)

    def stop_process_measurement(self) -> Dict[str, Any]:
        """
        Return empty process measurement result.

        Returns:
            Dict with zero values for all process-level metrics.
        """
        return {
            "instructions": 0,
            "cycles":        0,
            "ipc":           0.0,
        }
