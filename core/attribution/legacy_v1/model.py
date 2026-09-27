"""
core.attribution.legacy_v1.model -- LegacyV1AttributionModel.

Implements AttributionModelABC by calling the six ETL functions in the
same order and with the same arguments as experiment_runner.py did before
39.4b.  No math changes.  No SQL changes.  Rule S applies.

Call order mirrors save_pair() lines 1709-1813 and save_single() lines
2267-2286 and goal_execution_manager.py lines 739-762.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alems_sdk.attribution import AttributionModelABC, AttributionPolicy, AttributionError

# ETL imports: all from core.attribution.legacy_v1 sub-modules (moved verbatim).
from core.attribution.legacy_v1.phase_attribution_etl import compute_phase_attribution
from core.attribution.legacy_v1.aggregate_hardware_metrics import aggregate_hardware_metrics
from core.attribution.legacy_v1.energy_attribution_etl import compute_energy_attribution
from core.attribution.legacy_v1.duration_fix_etl import fix_run, fix_run_with_pretask
from core.attribution.legacy_v1.ttft_tpot_etl import populate_run as populate_ttft_tpot
import core.attribution.legacy_v1.goal_execution_etl as goal_execution_etl

if TYPE_CHECKING:
    from core.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Sentinel: these ETL functions need a raw conn because they were written
# before the writer contract existed.  They receive _wconn from the caller
# exactly as before.  Recorded as B39-4b-1 for cleanup in a post-Gate-F chunk.
_USES_RAW_CONN = True


class LegacyV1AttributionModel(AttributionModelABC):
    """
    Attribution model that reproduces pre-39.4b results exactly.

    Accepts an optional has_pretask flag and conn keyword argument so the
    runner can pass the same parameters it passed before.  This keeps the
    call sites in experiment_runner.py minimal-change.
    """

    def model_id(self):
        # type: () -> str
        return "legacy_v1"

    def model_version(self):
        # type: () -> str
        return "1.0.0"

    def supports_recompute(self):
        # type: () -> bool
        # UPDATE semantics: rerunning overwrites existing rows.
        return False

    def run_attribution(self, storage, run_id, policy):
        # type: (DatabaseManager, int, AttributionPolicy) -> None
        """
        Run all six ETL steps for a single run_id.

        storage must be a DatabaseManager instance.
        The raw conn is extracted here as a transitional measure (B39-4b-1).
        """
        if storage is None:
            raise AttributionError("run_attribution: storage handle is None")

        # Extract raw conn for legacy ETL functions that require it.
        # This is the ONLY place in the attribution contract that touches
        # conn directly.  All other code goes through storage.
        conn = storage.conn  # transitional exception B39-4b-1

        try:
            compute_phase_attribution(run_id, conn=conn)
            aggregate_hardware_metrics(run_id, conn=conn)
            compute_energy_attribution(run_id, conn=conn)
            populate_ttft_tpot(run_id, conn=conn)
            goal_execution_etl.process_one(run_id, conn)
        except Exception as exc:
            raise AttributionError(
                f"legacy_v1 attribution failed for run_id={run_id}: {exc}"
            ) from exc

        logger.debug("legacy_v1 run_attribution complete run_id=%d", run_id)

    def run_attribution_with_pretask(self, storage, run_id, policy,
                                     has_pretask, pretask_start_ns,
                                     pretask_end_ns, task_start_ns):
        # type: (DatabaseManager, AttributionPolicy, int, bool, int, int, int) -> None
        """
        Variant for runs that have a pre-task window (save_pair path).

        Calls fix_run_with_pretask instead of fix_run when has_pretask is True.
        Parameters mirror fix_run_with_pretask signature exactly.
        """
        conn = storage.conn  # transitional exception B39-4b-1

        try:
            if has_pretask:
                fix_run_with_pretask(
                    run_id,
                    pretask_start_ns=pretask_start_ns,
                    pretask_end_ns=pretask_end_ns,
                    task_start_ns=task_start_ns,
                    conn=conn,
                )
            else:
                fix_run(run_id, conn=conn)
        except Exception as exc:
            raise AttributionError(
                f"legacy_v1 duration fix failed for run_id={run_id}: {exc}"
            ) from exc

        logger.debug(
            "legacy_v1 duration fix complete run_id=%d has_pretask=%s",
            run_id, has_pretask,
        )
