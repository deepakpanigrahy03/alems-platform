# scripts/etl/goal_execution_etl.py -- SHIM (39.4b)
# Real implementation moved verbatim to core/attribution/legacy_v1/.
# This file re-exports everything so CLI backfill scripts keep working.
# Do not add logic here.
from core.attribution.legacy_v1.goal_execution_etl import *  # noqa: F401,F403
from core.attribution.legacy_v1.goal_execution_etl import process_one  # noqa: F401
