"""
core.attribution.legacy_v1 -- First-party attribution model plugin.

Wraps the six runtime ETL modules verbatim.  Math unchanged.  SQL unchanged.
Registered under alems.attribution.models as 'legacy_v1'.

This package is the ONLY attribution code the runtime calls after 39.4b.
scripts/etl/ shims import from here so CLI backfill scripts keep working.
"""
from core.attribution.legacy_v1.model import LegacyV1AttributionModel

# Convenience re-export so the entry point target resolves cleanly.
__all__ = ["LegacyV1AttributionModel"]

# Plugin metadata read by the registry (mirrors pyproject entry point).
ALEMS_PLUGIN_META = {
    "plugin_id":        "legacy_v1",
    "extension_point":  "alems.attribution.models",
    "family":           "semantic",
    "version":          "1.0.0",
    "sdk_range":        ">=1.0.0,<2.0.0",
    "description":      (
        "Legacy attribution model: wraps phase_attribution_etl, "
        "energy_attribution_etl, duration_fix_etl, ttft_tpot_etl, "
        "goal_execution_etl, aggregate_hardware_metrics verbatim. "
        "Identical results to pre-39.4b runs under exclusive isolation."
    ),
}
