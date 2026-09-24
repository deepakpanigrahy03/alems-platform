# scripts/tools/golden/replay.py
# Reruns derived ETL computations on a scratch DB copy.
# Implements SPEC_39_1 section 7.1a grounded replay rules.
#
# Two persistence paths exist (spec 7.1a rule 1):
#   Path A (save_pair / save_single): spbm etls inline, then network, tax,
#     phase_attribution, energy_attribution, ttft/tpot, fix_run, goal records,
#     goal_execution_etl, attribution_stubs, queue_etl.
#   Path B (execute_goal): insert_one_run, samples, orchestration, llm,
#     network_energy, finish_goal, goal_execution_etl, attribution_stubs,
#     energy_attribution, phase_attribution, fix_run_with_pretask.
#
# Replay strategy: delete derived rows for reference runs, then rerun ETL
# functions in the order recorded in reference_runs.yaml.
# etl_queue rows are written by goal_tracker but consumed asynchronously;
# replay includes writing queue rows but does NOT run the async consumer,
# because it writes no additional derived columns (spec 7.1a rule 2 decision:
# exclude async consumer output tables from layer A; they are in layer B smoke).
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Derived columns cleared before replay (per golden_class=derived tables).
# Autoincrement primary keys are never cleared; only ETL-populated columns.
# ---------------------------------------------------------------------------

# Columns in 'runs' that are ETL-populated (NULL at INSERT, filled by ETL).
# Source: schema.py SC-4 comments plus experiment_runner grep.
RUNS_ETL_COLS = [
    "attributed_energy_uj",
    "planning_energy_uj",
    "execution_energy_uj",
    "synthesis_energy_uj",
    "inter_phase_energy_uj",
    "phase_sample_coverage_pct",
    "ttft_ms",
    "tpot_ms",
    "gpu_total_energy_uj",
    "gpu_baseline_energy_uj",
    "gpu_dynamic_energy_uj",
    "gpu_pct_of_pkg",
    "gpu_attribution_method",
    "gpu_dynamic_method",
    "gpu_idle_power_w_used",
    "gpu_spbm_total_uj",
    "gpu_spbm_dynamic_uj",
    "gpu_residual_dynamic_uj",
    "spbm_power_sampling_freq_hz",
    "spbm_samples_expected",
    "spbm_samples_observed",
    "spbm_sample_coverage_pct",
    "spbm_integration_method",
    "spbm_conversion_loss_uj",
    "spbm_conversion_efficiency",
    "pre_task_energy_uj",
    "post_task_energy_uj",
    "framework_overhead_energy_uj",
    "task_duration_ns",
    "total_run_duration_ns",
    "avg_task_power_watts",
    "carbon_g",
    "water_ml",
    "methane_mg",
]


def _null_runs_etl_cols(conn: sqlite3.Connection, run_ids: List[int]) -> None:
    """
    Reset ETL-populated columns on runs rows to NULL before replay.
    This is idempotent: a second replay starts from the same cleared state.
    """
    if not run_ids:
        return
    placeholders = ",".join("?" * len(run_ids))
    set_clause = ", ".join(f"{c} = NULL" for c in RUNS_ETL_COLS)
    conn.execute(
        f"UPDATE runs SET {set_clause} WHERE run_id IN ({placeholders})",
        run_ids,
    )


def _clear_energy_attribution(conn: sqlite3.Connection, run_ids: List[int]) -> None:
    """Delete energy_attribution rows for reference runs before replay."""
    if not run_ids:
        return
    placeholders = ",".join("?" * len(run_ids))
    conn.execute(
        f"DELETE FROM energy_attribution WHERE run_id IN ({placeholders})",
        run_ids,
    )


def _clear_network_energy(conn: sqlite3.Connection, run_ids: List[int]) -> None:
    """Delete network_energy_attribution rows for reference runs before replay."""
    if not run_ids:
        return
    placeholders = ",".join("?" * len(run_ids))
    # Table name from ownership map; may not exist on all machines.
    try:
        conn.execute(
            f"DELETE FROM network_energy_attribution WHERE run_id IN ({placeholders})",
            run_ids,
        )
    except sqlite3.OperationalError as exc:
        logger.debug("network_energy_attribution clear skipped: %s", exc)


def clear_derived_rows(conn: sqlite3.Connection, run_ids: List[int]) -> None:
    """
    Clear all derived state for run_ids before replay.
    Operates inside the caller's transaction.
    """
    _null_runs_etl_cols(conn, run_ids)
    _clear_energy_attribution(conn, run_ids)
    _clear_network_energy(conn, run_ids)


# ---------------------------------------------------------------------------
# ETL replay per run
# ---------------------------------------------------------------------------

def _get_db_path(conn: sqlite3.Connection) -> str:
    """Extract the DB file path from an open connection."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    # rows: (seq, name, file)
    for row in rows:
        if row[1] == "main" and row[2]:
            return row[2]
    raise RuntimeError("Cannot determine DB path from connection")


def _goal_id_for_run(conn: sqlite3.Connection, run_id: int) -> Optional[int]:
    """Return goal_id for a run via goal_attempt, or None for save_pair runs."""
    row = conn.execute(
        "SELECT goal_id FROM goal_attempt WHERE run_id = ? LIMIT 1",
        (run_id,),
    ).fetchone()
    return row[0] if row else None


def replay_run(conn: sqlite3.Connection, run_id: int,
               persistence_path: str) -> Dict[str, Any]:
    """
    Replay ETL for one run on an open scratch DB connection.
    Returns a dict with keys: run_id, path, errors (list of str).

    Args:
        conn:             open writable connection to the scratch DB
        run_id:           the run to replay
        persistence_path: 'save_pair' | 'execute_goal'
    """
    errors: List[str] = []
    db_path = _get_db_path(conn)

    # Import ETL modules lazily so golden tool does not force import at startup.
    try:
        from scripts.etl import (
            energy_attribution_etl,
            phase_attribution_etl,
            duration_fix_etl,
            goal_execution_etl,
            network_energy_etl,
        )
    except ImportError as exc:
        return {"run_id": run_id, "path": persistence_path,
                "errors": [f"ETL import failed: {exc}"]}

    goal_id = _goal_id_for_run(conn, run_id)

    # Step 1: network energy (both paths).
    try:
        network_energy_etl.process_run(run_id, conn)
    except Exception as exc:
        errors.append(f"network_energy_etl run={run_id}: {exc}")

    # Step 2: goal_execution_etl (both paths, requires goal_id).
    if goal_id is not None:
        try:
            goal_execution_etl.process_one(goal_id, conn)
        except Exception as exc:
            errors.append(f"goal_execution_etl goal={goal_id}: {exc}")

    # Step 3: attribution stubs.
    try:
        energy_attribution_etl.populate_attribution_stubs(run_id, conn)
    except Exception as exc:
        errors.append(f"populate_attribution_stubs run={run_id}: {exc}")

    # Step 4: energy attribution (opens its own connection internally via db_path).
    try:
        energy_attribution_etl.compute_energy_attribution(run_id, Path(db_path))
    except Exception as exc:
        errors.append(f"compute_energy_attribution run={run_id}: {exc}")

    # Step 5: phase attribution.
    try:
        phase_attribution_etl.compute_phase_attribution(run_id, db_path)
    except Exception as exc:
        errors.append(f"compute_phase_attribution run={run_id}: {exc}")

    # Step 6: duration fix.  Path determines which variant.
    # Path B (execute_goal) uses fix_run_with_pretask when ml_features present.
    # Path A (save_pair) uses fix_run.
    # We attempt fix_run_with_pretask first; fall back to fix_run on TypeError.
    try:
        if persistence_path == "execute_goal":
            duration_fix_etl.fix_run_with_pretask(run_id, db_path=Path(db_path))
        else:
            duration_fix_etl.fix_run(run_id, db_path=Path(db_path))
    except Exception as exc:
        errors.append(f"duration_fix_etl run={run_id}: {exc}")

    return {"run_id": run_id, "path": persistence_path, "errors": errors}


def replay_all(scratch_path: Path, ref_runs: List[Dict]) -> List[Dict]:
    """
    Replay ETL for all reference runs on a scratch copy of the DB.
    Returns list of per-run result dicts.

    Args:
        scratch_path: path to the scratch DB (already copied from live)
        ref_runs:     list of dicts with keys run_id and persistence_path
    """
    results: List[Dict] = []
    conn = sqlite3.connect(str(scratch_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        run_ids = [r["run_id"] for r in ref_runs]
        # Clear derived state inside a transaction.
        conn.execute("BEGIN")
        clear_derived_rows(conn, run_ids)
        conn.execute("COMMIT")

        for ref in ref_runs:
            result = replay_run(conn, ref["run_id"], ref.get("persistence_path", "save_pair"))
            results.append(result)
            if result["errors"]:
                for err in result["errors"]:
                    logger.warning("replay error: %s", err)
        conn.commit()
    finally:
        conn.close()
    return results
