"""
alems_sdk — A-LEMS SDK public surface.

Stable facade for plugin authors. Depends on nothing in core.
"""
from alems_sdk.conformance import KitResult, ConformanceReport, run_conformance
from alems_sdk._kit_base import ConformanceKit
from alems_sdk._kit_measurement import MeasurementKit, MEASUREMENT_GROUPS
from alems_sdk._kit_execution import ExecutionKit, EXECUTION_GROUPS
from alems_sdk._kit_persistence import PersistenceKit, OutputKit, PERSISTENCE_GROUPS, OUTPUT_GROUPS
from alems_sdk.attribution import AttributionModelABC, AttributionPolicy, AttributionError

__all__ = [
    "KitResult",
    "ConformanceReport",
    "run_conformance",
    "ConformanceKit",
    "MeasurementKit",
    "MEASUREMENT_GROUPS",
    "ExecutionKit",
    "EXECUTION_GROUPS",
    "PersistenceKit",
    "OutputKit",
    "PERSISTENCE_GROUPS",
    "OUTPUT_GROUPS",
    "AttributionModelABC",
    "AttributionPolicy",
    "AttributionError",
]