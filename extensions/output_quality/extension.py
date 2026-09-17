"""
OUTPUT QUALITY EXTENSION  —  extensions/output_quality/extension.py
================================================================================

PURPOSE:
    Persistence boundary for scoring results (SPEC 35J). Writes
    output_quality and output_quality_judges rows from a JudgmentComputation.
    NEVER calls a scorer, NEVER recomputes a judgment — that is
    judgment_engine's job (core).

    Called directly and synchronously from experiment_runner.py's
    per-attempt loop via persist(), not via on_post_run() — scoring
    happens per-attempt, inline, during the run, and PostRunPayload is
    dispatched once per run, which cannot represent N per-attempt
    scores when a run has multiple attempts. See persist()'s docstring.

    Per INV-3 (Extension Isolation): this is the only code that writes to
    output_quality/output_quality_judges, both extension-owned tables.

    Column lists below match the REAL confirmed schema (gn100,
    schema_version=94, post-migration), not a re-derivation. task_id is
    intentionally left NULL — the original quality_judge.py never
    populated it either.

AUTHOR: Deepak Panigrahy (extraction/redesign for SPEC 35J)
================================================================================
"""

import logging
from typing import Optional

from core.execution.judgment_types import JudgmentComputation

logger = logging.getLogger(__name__)


class OutputQualityExtension:
    """
    Persist-only. Not a scorer — see judgment_engine.py for computation.
    """

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        return "output_quality"

    def get_tables(self):
        return ["output_quality", "output_quality_judges"]

    def get_migrations_dir(self):
        from pathlib import Path
        return Path(__file__).parent / "migrations"

    def on_activate(self) -> None:
        logger.info("OutputQualityExtension: activated")

    def on_deactivate(self) -> None:
        logger.info("OutputQualityExtension: deactivated")

    def on_post_run(self, payload) -> None:
        """
        Currently unused for output_quality: scoring happens per-attempt,
        inline, during the run (see experiment_runner.py's attempt loop),
        not once per completed run. PostRunPayload is dispatched once per
        run, which cannot represent N per-attempt scores for a run with
        multiple attempts. persist() below is the real entry point,
        called directly and synchronously from experiment_runner.py.
        This method exists to satisfy ExtensionABC's interface and is a
        no-op placeholder for a future batch/summary use, if one emerges.
        """
        return

    def persist(self, conn, attempt_id: int, goal_id: int, computation: JudgmentComputation) -> int:
        """
        The real entry point. Called directly, synchronously, right after
        judgment_engine.judge() returns for one attempt — not via
        on_post_run(). Returns quality_id so the caller can pass it to
        hallucination_detector.py exactly as the old inline code did.

        Never raises (AP-4) — caller should still proceed with its own
        attempt-completion logic even if persistence fails; catch at the
        call site, not here, since this method needs to return quality_id
        on success and the caller decides what "failed to persist" means
        for its own control flow.
        """
        quality_id = self._insert_output_quality(conn, attempt_id, goal_id, computation)
        for model, score, confidence, reasoning in computation.per_judge:
            self._insert_judge_row(
                conn=conn,
                quality_id=quality_id,
                attempt_id=attempt_id,
                goal_id=goal_id,
                judge_model=model or computation.judge_method,
                judge_score=score,
                judge_confidence=confidence,
                judge_reasoning=reasoning,
            )
        conn.commit()
        computation.result.quality_id = quality_id
        return quality_id

    def _insert_output_quality(self, conn, attempt_id: int, goal_id: int, computation: JudgmentComputation) -> int:
        """
        INSERT (not INSERT OR REPLACE — UNIQUE(attempt_id) removed in v094,
        so live and back-scored rows for the same attempt coexist by
        design; score_method distinguishes them).
        """
        cur = conn.execute(
            """
            INSERT INTO output_quality
                (attempt_id, goal_id, task_id, task_category, metric_type, raw_score,
                 normalized_score, pass_fail, judge_method, judge_count,
                 score_method, expected_output, actual_output,
                 energy_uj_at_judgment, manual_reviewed, judged_at,
                 scorer_version, scorer_config_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now'), ?, ?)
            """,
            (
                attempt_id,
                goal_id,
                computation.task_id,
                computation.task_category,
                computation.metric_type,
                computation.raw_score,
                computation.result.normalized_score,
                computation.result.pass_fail,
                computation.judge_method,
                computation.result.n_judges_used,
                computation.result.score_method,
                computation.expected_output,
                computation.actual_output,
                computation.energy_uj_at_judgment,
                None,  # scorer_version — TODO: source from scorer registry metadata
                None,  # scorer_config_hash — TODO: hash resolved config
            ),
        )
        return cur.lastrowid

    def _insert_judge_row(
        self,
        conn,
        quality_id: int,
        attempt_id: int,
        goal_id: int,
        judge_model: str,
        judge_score: float,
        judge_confidence: float,
        judge_reasoning: str,
    ) -> None:
        """
        INSERT one output_quality_judges row. Real schema has additional
        columns (judge_provider, judge_version, judge_temperature,
        judge_prompt_hash) not populated by the original quality_judge.py
        either — left NULL here for parity, not a regression.
        """
        conn.execute(
            """
            INSERT INTO output_quality_judges
                (quality_id, attempt_id, goal_id, judge_model,
                 judge_score, judge_confidence, judge_reasoning, judged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                quality_id, attempt_id, goal_id, judge_model,
                judge_score, judge_confidence, judge_reasoning,
            ),
        )
