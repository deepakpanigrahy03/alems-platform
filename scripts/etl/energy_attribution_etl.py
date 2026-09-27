# scripts/etl/energy_attribution_etl.py -- SHIM (39.4b)
# Real implementation moved verbatim to core/attribution/legacy_v1/.
# This file re-exports everything so CLI backfill scripts keep working.
# Do not add logic here.
from core.attribution.legacy_v1.energy_attribution_etl import *  # noqa: F401,F403
from core.attribution.legacy_v1.energy_attribution_etl import compute_energy_attribution  # noqa: F401
