# Re-exports from core.readers.*  (strangler facade, D2.2).
# Every symbol listed here is the SAME object as its core source (verified by test_sdk_reexports.py).
# Source paths recorded here for auditors; do not copy code.

from core.readers.interfaces import (       # core/readers/interfaces.py
    BaseReader,
    EnergyReaderABC,
    CPUReaderABC,
    ThermalReaderABC,
    DiskReaderABC,
    NICReaderABC,
)
from core.models.normalized_energy_reading import (  # core/models/normalized_energy_reading.py
    NormalizedEnergyReading,
)
from core.platform.adapter import (         # core/platform/adapter.py
    PlatformAdapterABC,
)

# Optional ABCs: import gracefully so the SDK installs even on machines
# where optional reader dependencies are absent.
try:
    from core.readers.interfaces import (
        ThermalReaderV2ABC,
        CoolingReaderABC,
        TurbostatReaderABC,
        MSRReaderABC,
        SchedulerMonitorABC,
    )
except ImportError:
    ThermalReaderV2ABC = None       # type: ignore[assignment,misc]
    CoolingReaderABC = None         # type: ignore[assignment,misc]
    TurbostatReaderABC = None       # type: ignore[assignment,misc]
    MSRReaderABC = None             # type: ignore[assignment,misc]
    SchedulerMonitorABC = None      # type: ignore[assignment,misc]

# Platform adapter result dataclasses are re-exported alongside the ABC
# so plugin authors need only one import.
try:
    from core.platform.adapter import (
        HardwareFingerprint,
        MeterInventory,
    )
except ImportError:
    HardwareFingerprint = None      # type: ignore[assignment,misc]
    MeterInventory = None           # type: ignore[assignment,misc]

__all__ = [
    "BaseReader",
    "EnergyReaderABC",
    "CPUReaderABC",
    "ThermalReaderABC",
    "ThermalReaderV2ABC",
    "CoolingReaderABC",
    "TurbostatReaderABC",
    "MSRReaderABC",
    "SchedulerMonitorABC",
    "DiskReaderABC",
    "NICReaderABC",
    "NormalizedEnergyReading",
    "PlatformAdapterABC",
    "HardwareFingerprint",
    "MeterInventory",
]
