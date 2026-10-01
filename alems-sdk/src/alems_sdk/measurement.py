"""
alems_sdk.measurement: one import point for the measurement family contracts.

Every name is a real object from an SDK module; nothing is conditional and
nothing is imported from core (G16, INV-14). Since 39.5.1a.2 the reader
contracts live in alems_sdk.readers, the reading type in
alems_sdk.energy_reading, the platform contract in alems_sdk.platforms.
HardwareFingerprint and MeterInventory were never defined anywhere and are
removed (G17); the detector output contract is the hw_config dict of
PlatformAdapterABC.detect().
"""

from alems_sdk.energy_reading import NormalizedEnergyReading
from alems_sdk.measurement_schema import DomainDescriptor, MeasurementSchema
from alems_sdk.platforms import (
    PlatformAdapterABC,
    ProvisionResult,
    ProvisionStep,
    VerificationCheck,
    VerificationResult,
)
from alems_sdk.readers import (
    BaseReader,
    CoolingReaderABC,
    CPUReaderABC,
    DiskReaderABC,
    EnergyReaderABC,
    MSRReaderABC,
    NICReaderABC,
    SchedulerMonitorABC,
    ThermalReaderABC,
    ThermalReaderV2ABC,
    TurbostatReaderABC,
)

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
    "DomainDescriptor",
    "MeasurementSchema",
    "PlatformAdapterABC",
    "ProvisionResult",
    "ProvisionStep",
    "VerificationCheck",
    "VerificationResult",
]
