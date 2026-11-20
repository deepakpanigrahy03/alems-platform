"""
Thermal sensor value-change probe (thermal_exposure extension).

The probe moved to the SDK (alems_sdk.thermal_probe) so that core
discovery (scripts/detect_thermal.py) and this extension share one
implementation without core importing an extension. This module keeps the
earlier import path working, with unchanged behaviour.
"""

from alems_sdk.thermal_probe import PROGRESS_EVERY_S, observe_value_changes  # noqa: F401

__all__ = ["observe_value_changes", "PROGRESS_EVERY_S"]
