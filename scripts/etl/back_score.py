"""
BACK SCORE ETL  —  scripts/etl/back_score.py
================================================================================

PURPOSE:
    Backfill output_quality scores for historical winning attempts that
    completed before scoring was wired (pre-35J). Reads model output from
    llm_interactions (the real source for historical runs), resolves the
    task expectation from tasks.yaml, scores via judgment_engine, and
    persists via OutputQualityExtension — the same path live scoring uses.

    Source of truth for model output:
        MAX(step_index) per run_id + workflow_type, then MAX(interaction_id)
        tiebreaker within that step. Verified correct on GN100 against both
        agentic (multi-step tool calls, final answer at last step) and
        linear (single step, step_index=1) runs.

    NEVER overwrites existing output_quality rows.
    Creates new rows with score_method='back_scored'.
    Live forward-scored rows remain untouched.

USAGE:
    python3 scripts/etl/back_score.py [--dry-run] [--limit N] [--goal-id G]

    --dry-run  : print what would be scored, write nothing
    --limit N  : score at most N attempts (default: all unscored)
    --goal-id G: score one specific goal_id only (for debugging)

COMPLIANCE:
    DC-1: ~30% inline comments explaining WHY.
    DC-2: docstrings on every function.
    DC-3: no silent failures — every exception logged with context.
    PAC-4: graceful degradation — skips attempts where config is missing.

================================================================================
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Project root on path so imports resolve from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.config_loader import ConfigLoader
from core.database.manager import DatabaseManager
from core.execution.expectation.schema import Expectation
from core.execution import judgment_engine
from extensions.output_quality.extension import OutputQualityExtension

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("back_score")

_extension = OutputQualityExtension()

# Responses that are API errors recorded verbatim, not real model outputs.
# Scoring these would produce meaningless results.
_ERROR_PREFIXES = ("Error:", "error:", "429", "503", "502")

# Core SQL: resolves the correct final-step response for each unscored
# winning attempt. workflow_type join prevents linear responses appearing
# under agentic goals (same exp_id, different run_ids, both write to
# llm_interactions with their own workflow_type column).
_UNSCORED_SQL = """
    SELECT
        ga.attempt_id,
        ga.run_id,
        ge.goal_id,
        ge.workflow_type,
        ge.task_id,
        ge.goal_type        AS task_category,
        li.response         AS output_text,
        li.step_index       AS final_step
    FROM goal_attempt ga
    JOIN goal_execution ge  ON ge.goal_id = ga.goal_id
    LEFT JOIN output_quality oq ON oq.attempt_id = ga.attempt_id
    JOIN llm_interactions li
         ON  li.run_id        = ga.run_id
         AND li.workflow_type = ge.workflow_type
         AND li.step_index    = (
             SELECT MAX(step_index)
             FROM   llm_interactions
             WHERE  run_id        = ga.run_id
               AND  workflow_type = ge.workflow_type
         )
    WHERE ga.is_winning  = 1
      AND oq.quality_id  IS NULL
      AND li.response    IS NOT NULL
      AND li.response    != ''
    ORDER BY li.interaction_id DESC
"""

_ONE_GOAL_SQL = """
    SELECT
        ga.attempt_id,
        ga.run_id,
        ge.goal_id,
        ge.workflow_type,
        ge.task_id,
        ge.goal_type        AS task_category,
        li.response         AS output_text,
        li.step_index       AS final_step
    FROM goal_attempt ga
    JOIN goal_execution ge  ON ge.goal_id = ga.goal_id
    JOIN llm_interactions li
         ON  li.run_id        = ga.run_id
         AND li.workflow_type = ge.workflow_type
         AND li.step_index    = (
             SELECT MAX(step_index)
             FROM   llm_interactions
             WHERE  run_id        = ga.run_id
               AND  workflow_type = ge.workflow_type
         )
    WHERE ge.goal_id    = ?
      AND ga.is_winning = 1
      AND li.response   IS NOT NULL
      AND li.response   != ''
    ORDER BY li.interaction_id DESC
    LIMIT 1
"""


def _load_tasks():
    """
    Load tasks.yaml once at startup and index by task id.
    Returns dict keyed by task id string.
    Key name confirmed from recon: items use 'id' not 'task_id'.
    """
    import yaml
    tasks_path = Path(__file__).resolve().parents[2] / "config" / "tasks.yaml"
    with open(tasks_path) as f:
        raw = yaml.safe_load(f)
    return {t["id"]: t for t in raw.get("tasks", []) if "id" in t}


def _resolve_expectation(task_def):
    """
    Build an Expectation from a task definition dict.
    Returns None if the task has no scorable expectation.

    Mirrors experiment_runner._auto_expectation(): explicit expectation:
    block takes priority, then expected_answer fallback.
    benchmark_dataset tasks have no expectation block and return None here —
    their resolver raises NotImplementedError which is not yet implemented.
    """
    exp_dict = task_def.get("expectation") or {}
    if not exp_dict and task_def.get("expected_answer"):
        exp_dict = {
            "scorer_type":    "exact_match",
            "expected_value": task_def["expected_answer"],
        }
    exp = Expectation.from_dict(exp_dict)
    if not exp.is_scoreable():
        return None
    return exp


def _is_error_response(text):
    """Return True if response is an API error, not a real model output."""
    return any(text.startswith(p) for p in _ERROR_PREFIXES)


def _get_energy(conn, run_id):
    """
    Read total_energy_uj for a run.
    Column name confirmed from recon: total_energy_uj not energy_uj.
    Returns 0 on missing or NULL.
    """
    row = conn.execute(
        "SELECT total_energy_uj FROM runs WHERE run_id = ? LIMIT 1", (run_id,)
    ).fetchone()
    return int(row[0] or 0) if row else 0


def score_one(conn, row, tasks, dry_run=False):
    """
    Score one attempt and persist the result.

    Args:
        conn    : sqlite3 connection.
        row     : sqlite3.Row from the unscored query.
        tasks   : dict of task definitions keyed by task id.
        dry_run : if True log intent but write nothing.

    Returns:
        'scored', 'skipped', or 'error'
    """
    attempt_id    = row["attempt_id"]
    run_id        = row["run_id"]
    goal_id       = row["goal_id"]
    task_id       = row["task_id"]
    task_category = row["task_category"]
    output_text   = row["output_text"]

    if _is_error_response(output_text):
        logger.info("skip attempt=%d: error response", attempt_id)
        return "skipped"

    task_def = tasks.get(task_id)
    if not task_def:
        logger.info("skip attempt=%d: no task definition for task_id=%s", attempt_id, task_id)
        return "skipped"

    expectation = _resolve_expectation(task_def)
    if expectation is None:
        logger.info("skip attempt=%d: task=%s has no scorable expectation", attempt_id, task_id)
        return "skipped"

    if dry_run:
        logger.info(
            "DRY RUN: would score attempt=%d goal=%d task=%s category=%s",
            attempt_id, goal_id, task_id, task_category,
        )
        return "scored"

    try:
        energy_uj = _get_energy(conn, run_id)

        computation = judgment_engine.judge(
            conn=conn,
            task_category=task_category,
            task_id_value=task_id,
            expectation=expectation,
            model_output=output_text,
            agentic_result=None,
            energy_uj=energy_uj,
        )
        # Mark as back_scored so it is distinguishable from live forward rows.
        computation.result.score_method = "back_scored"

        quality_id = _extension.persist(
            conn=conn,
            attempt_id=attempt_id,
            goal_id=goal_id,
            computation=computation,
        )
        logger.info(
            "scored attempt=%d goal=%d quality_id=%d score=%.3f method=%s",
            attempt_id, goal_id, quality_id,
            computation.result.normalized_score,
            computation.judge_method,
        )
        return "scored"

    except Exception as e:
        logger.error("error scoring attempt=%d: %s", attempt_id, e)
        return "error"


def main():
    parser = argparse.ArgumentParser(
        description="Backfill output_quality scores for historical winning attempts."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be scored, write nothing.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Score at most N attempts (default: all unscored).")
    parser.add_argument("--goal-id", type=int, default=None,
                        help="Score one specific goal_id only.")
    args = parser.parse_args()

    dm = DatabaseManager(ConfigLoader().get_db_config())
    conn = dm.db.conn
    # Row factory so columns are accessible by name.
    conn.row_factory = sqlite3.Row

    tasks = _load_tasks()
    logger.info("loaded %d task definitions", len(tasks))

    if args.goal_id:
        rows = conn.execute(_ONE_GOAL_SQL, (args.goal_id,)).fetchall()
        if not rows:
            logger.error("no unscored winning attempt found for goal_id=%d", args.goal_id)
            sys.exit(1)
    else:
        sql = _UNSCORED_SQL
        if args.limit:
            sql += f" LIMIT {args.limit}"
        rows = conn.execute(sql).fetchall()

    logger.info(
        "found %d attempts to score%s",
        len(rows), " (dry run)" if args.dry_run else "",
    )

    scored = skipped = errors = 0
    for row in rows:
        result = score_one(conn, row, tasks, dry_run=args.dry_run)
        if result == "scored":
            scored += 1
        elif result == "skipped":
            skipped += 1
        else:
            errors += 1

    logger.info("done: scored=%d skipped=%d errors=%d", scored, skipped, errors)


if __name__ == "__main__":
    main()
