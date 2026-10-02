"""
scripts/etl/energy_window_resolver.py (compatibility shim)

Moved to core/attribution/energy_window.py in 39.5.1 1d.1 because core must
not import scripts (CH39-3). This shim keeps old script imports working and
is removed in 1f once no caller remains.
"""

from core.attribution.energy_window import (  # noqa: F401  re export only
    EnergyWindowResolverABC,
    EnergyWindowResolverFactory,
    IokitV2Resolver,
    NullResolver,
    RaplLegacyResolver,
    Sample,
    SpbmV2Resolver,
    WindowEnergy,
    WindowEnergyResult,
    energy_in_window,
    window_energy_for_run,
)
