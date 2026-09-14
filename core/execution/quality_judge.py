"""
================================================================================
QUALITY JUDGE  —  core/execution/quality_judge.py
================================================================================

PURPOSE:
    Orchestrates N-judge quality scoring for LLM outputs.
    Uses ScorerRegistry to dispatch to the correct scorer per task category.
    Writes output_quality and output_quality_judges rows.

    Called by experiment_runner after each attempt is saved — AFTER core
    energy_uj is committed. The judge call's energy does not contaminate
    the inference measurement (Observer Energy separation).

    N-judge median reconciliation (v2):
        N=1 → single_judge (score as-is)
        N>=2 → median-based reconciliation:
            All within MEDIAN_TIGHT_BAND of median → 'averaged', mean(scores)
            All within MEDIAN_LOOSE_BAND of median → 'consensus_median', median
            N-1 within tight band, 1 outlier      → 'majority_median', median(agreeing)
            Otherwise                              → 'needs_review', score=None

AUTHOR: Deepak Panigrahy
================================================================================
"""

import json
import logging
import statistics
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.execution.scorers.bootstrap import scorer_registry, register_all_scorers

logger = logging.getLogger(__name__)

# Register scorers at import time — idempotent.
register_all_scorers()

# Acceptance threshold: normalized_score >= this → pass_fail=1.
# Tied to output_quality_normalization_v1 provenance method.
ACCEPTANCE_THRESHOLD = 0.7

# Median reconciliation bands.
MEDIAN_TIGHT_BAND = 0.20   # all within ±0.20 → 'averaged'
MEDIAN_LOOSE_BAND = 0.40   # all within ±0.40 → 'consensus_median'
OUTLIER_DROP_BAND = 0.20   # N-1 within tight, 1 outside → 'majority_median'


@dataclass
class JudgmentResult:
    """
    Result of one quality judgment cycle for one attempt.

    normalized_score: float [0.0, 1.0] or None when needs_review.
    pass_fail       : 1 (pass) or 0 (fail) or None when needs_review.
    score_method    : how scores were reconciled across N judges.
    quality_id      : primary key of the inserted output_quality row.
    n_judges_used   : number of judges that returned valid scores.
    """

    normalized_score: Optional[float]
    pass_fail: Optional[int]
    score_method: str
    quality_id: int
    n_judges_used: int


class QualityJudge:
    """
    N-judge quality scoring orchestrator.

    Instantiate once per experiment session and reuse across attempts.
    Thread-safe — no mutable state after construction.
    """

    def judge(
        self,
        conn,
        attempt_id: int,
        goal_id: int,
        task_category: str,
        actual_output: str,
        expected_output: str,
        energy_uj: int,
    ) -> JudgmentResult:
        """
        Score one attempt using N judges from task_quality_config.

        Steps:
        1. Load task_quality_config for task_category.
        2. Call score() N times (n_judges from config).
        3. Filter failed scores (confidence=0.0).
        4. Reconcile using median bands.
        5. INSERT output_quality row.
        6. INSERT output_quality_judges rows (one per judge, including failed).
        7. Return JudgmentResult.

        Args:
            conn          : sqlite3 connection.
            attempt_id    : goal_attempt primary key.
            goal_id       : goal_execution primary key.
            task_category : task category string (e.g. "reasoning").
            actual_output : LLM's actual response text.
            expected_output: Reference answer text.
            energy_uj     : energy_uj of the inference run (stored for context).

        Returns:
            JudgmentResult with quality_id set.
        """
        # Load config for this task category.
        config = self._load_config(conn, task_category)
        if config is None:
            # No config for this category — skip scoring.
            logger.warning(
                "QualityJudge: no task_quality_config for category='%s' — skipping",
                task_category,
            )
            return self._insert_skipped(conn, attempt_id, goal_id, task_category, energy_uj)

        metric_type = config["metric_type"]
        judge_method = config["judge_method"]
        n_judges = config.get("n_judges", 1)
        judge_model_set = self._parse_model_set(config.get("judge_model_set"))
        rubric = self._parse_rubric(config.get("rubric"))
        threshold = config.get("threshold", ACCEPTANCE_THRESHOLD)

        # Get scorer from registry.
        try:
            scorer = scorer_registry.get(judge_method)
        except Exception as exc:
            logger.error(
                "QualityJudge: no scorer for judge_method='%s': %s — skipping",
                judge_method,
                exc,
            )
            return self._insert_skipped(conn, attempt_id, goal_id, task_category, energy_uj)

        # Call scorer N times.
        raw_scores: List[Tuple[float, float, str]] = []
        for i in range(n_judges):
            model = judge_model_set[i] if judge_model_set and i < len(judge_model_set) else None
            try:
                result = scorer.score(
                    actual=actual_output or "",
                    expected=expected_output or "",
                    judge_model=model,
                    rubric=rubric,
                )
                raw_scores.append(result)
            except Exception as exc:
                logger.error("QualityJudge: scorer.score raised for judge %d: %s", i, exc)
                raw_scores.append((0.0, 0.0, "scorer_failed"))

        # Filter valid scores (confidence > 0 means scorer ran successfully).
        valid_scores = [(s, c, r) for s, c, r in raw_scores if c > 0.0]
        n_valid = len(valid_scores)

        # Reconcile scores.
        normalized_score, score_method = self._reconcile(
            [s for s, c, r in valid_scores], n_judges
        )

        # Compute pass_fail.
        if normalized_score is None:
            pass_fail = None
        else:
            pass_fail = 1 if normalized_score >= threshold else 0

        # Compute raw_score (mean of valid scores before reconciliation).
        raw_score = statistics.mean([s for s, c, r in valid_scores]) if valid_scores else None

        # Compute agreement_score (std dev of valid scores — lower = more agreement).
        if len(valid_scores) >= 2:
            agreement_score = 1.0 - min(1.0, statistics.stdev([s for s, c, r in valid_scores]))
        elif len(valid_scores) == 1:
            agreement_score = 1.0  # single judge — perfect agreement with itself
        else:
            agreement_score = None

        # INSERT output_quality row.
        quality_id = self._insert_output_quality(
            conn=conn,
            attempt_id=attempt_id,
            goal_id=goal_id,
            task_category=task_category,
            metric_type=metric_type,
            judge_method=judge_method,
            raw_score=raw_score,
            normalized_score=normalized_score,
            pass_fail=pass_fail,
            score_method=score_method,
            agreement_score=agreement_score,
            judge_count=n_valid,
            expected_output=expected_output,
            actual_output=actual_output,
            energy_uj_at_judgment=energy_uj,
        )

        # INSERT output_quality_judges rows (one per raw score).
        for i, (score, confidence, reasoning) in enumerate(raw_scores):
            model = judge_model_set[i] if judge_model_set and i < len(judge_model_set) else None
            self._insert_judge_row(
                conn=conn,
                quality_id=quality_id,
                attempt_id=attempt_id,
                goal_id=goal_id,
                judge_model=model or judge_method,
                judge_score=score,
                judge_confidence=confidence,
                judge_reasoning=reasoning,
            )

        conn.commit()
        logger.debug(
            "QualityJudge: attempt_id=%d score=%.3f method=%s n_valid=%d/%d",
            attempt_id,
            normalized_score if normalized_score is not None else -1,
            score_method,
            n_valid,
            n_judges,
        )

        return JudgmentResult(
            normalized_score=normalized_score,
            pass_fail=pass_fail,
            score_method=score_method,
            quality_id=quality_id,
            n_judges_used=n_valid,
        )

    def _reconcile(
        self,
        scores: List[float],
        n_judges_requested: int,
    ) -> Tuple[Optional[float], str]:
        """
        Median-based reconciliation for N valid scores.

        Returns (normalized_score, score_method).

        N=0 → (None, 'needs_review')
        N=1 → (score[0], 'single_judge')
        N>=2 → median bands:
            All within MEDIAN_TIGHT_BAND of median → 'averaged', mean(scores)
            All within MEDIAN_LOOSE_BAND of median → 'consensus_median', median
            N-1 within tight band, 1 outlier       → 'majority_median', median(agreeing)
            Otherwise                              → 'needs_review', None
        """
        if not scores:
            return (None, "needs_review")

        if len(scores) == 1:
            return (scores[0], "single_judge")

        med = statistics.median(scores)
        deviations = [abs(s - med) for s in scores]

        # All within tight band → averaged (unanimous agreement).
        if all(d <= MEDIAN_TIGHT_BAND for d in deviations):
            return (statistics.mean(scores), "averaged")

        # All within loose band → consensus median.
        if all(d <= MEDIAN_LOOSE_BAND for d in deviations):
            return (med, "consensus_median")

        # N-1 within tight band, 1 outlier → majority median.
        outliers = [i for i, d in enumerate(deviations) if d > MEDIAN_TIGHT_BAND]
        if len(outliers) == 1:
            agreeing = [s for i, s in enumerate(scores) if i not in outliers]
            return (statistics.median(agreeing), "majority_median")

        # Inter-rater disagreement — human review needed.
        return (None, "needs_review")

    def _load_config(self, conn, task_category: str) -> Optional[dict]:
        """Load task_quality_config row for this category."""
        try:
            row = conn.execute(
                """
                SELECT metric_type, judge_method, threshold,
                       n_judges, judge_model_set, rubric
                FROM task_quality_config
                WHERE task_category = ?
                LIMIT 1
                """,
                (task_category,),
            ).fetchone()
            if row is None:
                return None
            return {
                "metric_type": row[0],
                "judge_method": row[1],
                "threshold": row[2],
                "n_judges": row[3] or 1,
                "judge_model_set": row[4],
                "rubric": row[5],
            }
        except Exception as exc:
            logger.error("QualityJudge._load_config failed: %s", exc)
            return None

    def _parse_model_set(self, judge_model_set_json: Optional[str]) -> Optional[List[str]]:
        """Parse JSON array of judge model IDs, or None."""
        if not judge_model_set_json:
            return None
        try:
            models = json.loads(judge_model_set_json)
            return models if isinstance(models, list) else None
        except Exception:
            return None

    def _parse_rubric(self, rubric_json: Optional[str]) -> Optional[dict]:
        """Parse JSON rubric dict, or None."""
        if not rubric_json:
            return None
        try:
            return json.loads(rubric_json)
        except Exception:
            return None

    def _insert_output_quality(
        self,
        conn,
        attempt_id: int,
        goal_id: int,
        task_category: Optional[str],
        metric_type: str,
        judge_method: str,
        raw_score: Optional[float],
        normalized_score: Optional[float],
        pass_fail: Optional[int],
        score_method: str,
        agreement_score: Optional[float],
        judge_count: int,
        expected_output: Optional[str],
        actual_output: Optional[str],
        energy_uj_at_judgment: Optional[int],
    ) -> int:
        """INSERT output_quality row. Returns quality_id."""
        cur = conn.execute(
            """
            INSERT OR REPLACE INTO output_quality
                (attempt_id, goal_id, task_category, metric_type, raw_score,
                 normalized_score, pass_fail, judge_method, judge_count,
                 agreement_score, score_method, expected_output, actual_output,
                 energy_uj_at_judgment, manual_reviewed, judged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))
            """,
            (
                attempt_id, goal_id, task_category, metric_type, raw_score,
                normalized_score, pass_fail, judge_method, judge_count,
                agreement_score, score_method, expected_output, actual_output,
                energy_uj_at_judgment,
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
        """INSERT one output_quality_judges row."""
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

    def _insert_skipped(
        self,
        conn,
        attempt_id: int,
        goal_id: int,
        task_category: str,
        energy_uj: int,
    ) -> JudgmentResult:
        """
        Insert a placeholder row when scoring cannot run.
        Returns JudgmentResult with score=None, pass_fail=None.
        """
        cur = conn.execute(
            """
            INSERT OR REPLACE INTO output_quality
                (attempt_id, goal_id, task_category, metric_type, raw_score,
                 normalized_score, pass_fail, judge_method, judge_count,
                 score_method, energy_uj_at_judgment, manual_reviewed, judged_at)
            VALUES (?, ?, ?, 'binary', NULL, NULL, NULL, 'exact_match', 0,
                    'needs_review', ?, 0, datetime('now'))
            """,
            (attempt_id, goal_id, task_category, energy_uj),
        )
        conn.commit()
        return JudgmentResult(
            normalized_score=None,
            pass_fail=None,
            score_method="needs_review",
            quality_id=cur.lastrowid,
            n_judges_used=0,
        )
