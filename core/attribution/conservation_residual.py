"""
conservation_residual.py -- Compute and persist attribution_residual rows.

Called after the full ETL chain completes for a run.  Report only in
foundation phases -- never fails or alters a run (SPEC_39_4 section 7, INV-12).

Domain map:
    pkg    runs.pkg_energy_uj    vs  ea.pkg_energy_uj
    core   runs.core_energy_uj   vs  ea.core_energy_uj
    dram   runs.dram_energy_uj   vs  ea.dram_energy_uj
    uncore runs.uncore_energy_uj  vs  ea.uncore_energy_uj
    total  runs.total_energy_uj  vs  sum of all ea phase columns

Tolerance: max(10000 uj, 1% of measured).
A negative residual means over-attribution (Bug-8 post-task window overlap).
"""

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

_MIN_TOLERANCE_UJ = 10_000
_TOLERANCE_FRACTION = 0.01

# Phase columns that together account for dynamic energy in energy_attribution.
_PHASE_COLUMNS = [
    "planning_energy_uj",
    "execution_energy_uj",
    "synthesis_energy_uj",
    "inter_phase_energy_uj",
    "orchestration_energy_uj",
    "llm_compute_energy_uj",
    "tool_energy_uj",
    "retry_energy_uj",
    "failed_tool_energy_uj",
    "rejected_generation_energy_uj",
    "llm_wait_energy_uj",
    "unattributed_energy_uj",
]

# (domain_label, runs_column, list_of_ea_columns)
_DOMAIN_MAP = [
    ("pkg",    "pkg_energy_uj",    ["pkg_energy_uj"]),
    ("core",   "core_energy_uj",   ["core_energy_uj"]),
    ("dram",   "dram_energy_uj",   ["dram_energy_uj"]),
    ("uncore", "uncore_energy_uj", ["uncore_energy_uj"]),
    ("total",  "total_energy_uj",  _PHASE_COLUMNS),
]


def _tolerance(measured_uj: int) -> int:
    # type: (int) -> int
    return max(_MIN_TOLERANCE_UJ, int(abs(measured_uj) * _TOLERANCE_FRACTION))


def _status(measured: int, residual: int, tolerance: int) -> str:
    # type: (int, int, int) -> str
    if measured == 0:
        return "not_applicable"
    if abs(residual) <= tolerance:
        return "ok"
    if residual < 0:
        return "over_attributed"
    return "under_attributed"


def compute_conservation_residual(run_id: int, conn: object) -> None:
    # type: (int, object) -> None
    """
    Compute and insert attribution_residual rows for one run.

    Args:
        run_id: Committed run_id after ETL chain completes.
        conn:   Raw sqlite3 connection (transitional -- backlog B39-4c-1).

    Never raises -- errors logged and skipped (SPEC_39_4 section 7).
    """
    try:
        _compute_and_insert(run_id, conn)
    except Exception as exc:
        logger.warning(
            "conservation_residual: run_id=%d skipped: %s", run_id, exc
        )


def _compute_and_insert(run_id: int, conn: object) -> None:
    # type: (int, object) -> None

    runs_row = conn.execute(
        """
        SELECT total_energy_uj, pkg_energy_uj, core_energy_uj,
               dram_energy_uj, uncore_energy_uj
        FROM runs WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()

    if runs_row is None:
        logger.warning("conservation_residual: no runs row for run_id=%d", run_id)
        return

    runs_total, runs_pkg, runs_core, runs_dram, runs_uncore = runs_row

    ea_col_names = (
        ["pkg_energy_uj", "core_energy_uj", "dram_energy_uj", "uncore_energy_uj"]
        + _PHASE_COLUMNS
    )
    ea_select = ", ".join(ea_col_names)

    ea_row = conn.execute(
        "SELECT {} FROM energy_attribution WHERE run_id = ?".format(ea_select),
        (run_id,),
    ).fetchone()

    if ea_row is None:
        logger.debug(
            "conservation_residual: no energy_attribution row for run_id=%d", run_id
        )
        return

    ea_vals = dict(zip(ea_col_names, ea_row))

    runs_vals = {
        "total_energy_uj":  runs_total  or 0,
        "pkg_energy_uj":    runs_pkg    or 0,
        "core_energy_uj":   runs_core   or 0,
        "dram_energy_uj":   runs_dram   or 0,
        "uncore_energy_uj": runs_uncore or 0,
    }

    rows = []  # type: List[Tuple]
    for domain_label, runs_col, ea_cols in _DOMAIN_MAP:
        measured   = runs_vals.get(runs_col) or 0
        attributed = sum(ea_vals.get(c) or 0 for c in ea_cols)
        residual   = measured - attributed
        tolerance  = _tolerance(measured)
        status     = _status(measured, residual, tolerance)
        rows.append((
            run_id, domain_label, "task_window",
            measured, attributed, residual, tolerance, status,
        ))

    # Idempotent: delete then reinsert.
    conn.execute("DELETE FROM attribution_residual WHERE run_id = ?", (run_id,))
    conn.executemany(
        """
        INSERT INTO attribution_residual
            (run_id, domain, window_label, measured_uj, attributed_uj,
             residual_uj, tolerance_uj, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()

    for row in rows:
        _, domain, _, measured, attributed, residual, tolerance, status = row
        logger.info(
            "conservation_residual: run_id=%d domain=%-6s "
            "measured=%d attributed=%d residual=%d status=%s",
            run_id, domain, measured, attributed, residual, status,
        )
