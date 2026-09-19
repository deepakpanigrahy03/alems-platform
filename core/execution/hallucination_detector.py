"""
================================================================================
HALLUCINATION DETECTOR  —  core/execution/hallucination_detector.py
================================================================================

PURPOSE:
    Classifies failed attempts (pass_fail=0) as hallucinatory and writes
    hallucination_events rows. Uses ontology_registry.py for type and
    method taxonomy — never hardcodes type strings.

    Called by experiment_runner after QualityJudge.judge() when pass_fail=0.
    Only inserts a row when a hallucination is confidently detected.
    Returns False (and inserts nothing) for 'needs_review' cases.

DETECTION LOGIC:
    exact_match failed  → hallucination_type='factual_error', method='exact_match'
    llm_judge < 0.3     → hallucination_type='fabrication', method='llm_judge'
    semantic < 0.3      → hallucination_type='factual_error', method='semantic'
    needs_review        → no insert, return False

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional

from core.ontology_registry import (
    HALLUCINATION_TYPES,
    DETECTION_METHODS,
    validate_hallucination_type,
    validate_detection_method,
)
from core.execution.judgment_types import JudgmentResult

logger = logging.getLogger(__name__)

# Score threshold below which llm_judge scores indicate fabrication.
_FABRICATION_THRESHOLD = 0.3

# Score threshold below which semantic scores indicate factual error.
_SEMANTIC_ERROR_THRESHOLD = 0.3

# Severity thresholds based on normalized score.
# Lower score → higher severity.
_SEVERITY_CRITICAL_THRESHOLD = 0.2   # score < 0.2 → critical
_SEVERITY_HIGH_THRESHOLD = 0.4       # score < 0.4 → high
_SEVERITY_MEDIUM_THRESHOLD = 0.6     # score < 0.6 → medium
# score >= 0.6 but pass_fail=0 → low severity


class HallucinationDetector:
    """
    Detects and classifies hallucinations from failed quality judgments.

    Stateless — instantiate once per session and reuse across attempts.
    """

    def detect(
        self,
        conn,
        attempt_id: int,
        goal_id: int,
        actual_output: str,
        expected_output: str,
        judgment: JudgmentResult,
        interaction_id: Optional[int] = None,
        orchestration_event_id: Optional[int] = None,
    ) -> bool:
        """
        Detect and record a hallucination for a failed attempt.

        Only called when pass_fail=0. Returns True if a hallucination
        row was inserted, False if detection was inconclusive (needs_review).

        Args:
            conn                  : sqlite3 connection.
            attempt_id            : goal_attempt primary key.
            goal_id               : goal_execution primary key.
            actual_output         : LLM's actual response.
            expected_output       : Reference answer.
            judgment              : JudgmentResult from QualityJudge.
            interaction_id        : FK to llm_interactions (optional).
            orchestration_event_id: FK to orchestration_events (optional).

        Returns:
            True if hallucination_events row inserted.
            False if inconclusive or error.
        """
        try:
            # Do not insert for needs_review — inconclusive.
            if judgment.score_method == "needs_review" or judgment.normalized_score is None:
                logger.debug(
                    "HallucinationDetector: attempt_id=%d score_method=needs_review — skipping",
                    attempt_id,
                )
                return False

            # Classify hallucination type and detection method.
            hallucination_type, detection_method, confidence = self._classify(judgment)

            if hallucination_type is None:
                # Detection inconclusive — score too ambiguous.
                return False

            # Validate against ontology (will raise ValueError if invalid).
            validate_hallucination_type(hallucination_type)
            validate_detection_method(detection_method)

            # Compute severity.
            severity = self._compute_severity(judgment.normalized_score)

            # Compute wasted energy for this attempt.
            wasted_energy = self._compute_wasted_energy(attempt_id, conn)

            # Compute semantic similarity for the events record.
            semantic_sim = self._compute_semantic_similarity(actual_output, expected_output)

            # INSERT hallucination_events row.
            conn.execute(
                """
                INSERT INTO hallucination_events
                    (attempt_id, goal_id, hallucination_type, detection_method,
                     detection_confidence, semantic_similarity, severity,
                     wasted_energy_uj_real, interaction_id, orchestration_event_id,
                     detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    attempt_id, goal_id, hallucination_type, detection_method,
                    confidence, semantic_sim, severity,
                    wasted_energy, interaction_id, orchestration_event_id,
                ),
            )
            conn.commit()

            logger.debug(
                "HallucinationDetector: attempt_id=%d type=%s method=%s severity=%s",
                attempt_id, hallucination_type, detection_method, severity,
            )
            return True

        except Exception as exc:
            logger.error(
                "HallucinationDetector.detect failed for attempt_id=%d: %s",
                attempt_id,
                exc,
            )
            return False

    def _classify(
        self,
        judgment: JudgmentResult,
    ):
        """
        Classify reasoning failure type and detection method from judgment.
        Output types are taxonomy-aligned (failure_taxonomy.failure_type_id).

        Three taxonomy types produced here (reasoning domain only):
            hallucination   — LLM confidently fabricated content (score < 0.3)
            semantic_error  — LLM output syntactically valid but wrong (0.3-0.5)
            capability_error— LLM attempted task beyond its capability (exact_match=0)

        These types are DETECTED, never injected.
        ScenarioInjector refuses to inject reasoning-domain types.

        Returns (failure_type_id, detection_method, confidence) or
        (None, None, None) if inconclusive.
        """
        score = judgment.normalized_score
        method = judgment.score_method

        # exact_match score=0 → model attempted but produced entirely wrong
        # answer type — capability_error (attempted beyond capability).
        # Distinct from hallucination: model understood the question but
        # produced a categorically wrong output (wrong format, wrong domain).
        if method == "single_judge" and score == 0.0:
            return ("capability_error", "exact_match", 1.0)

        # llm_judge or semantic score very low → hallucination.
        # Model confidently produced fabricated content.
        # Previously 'fabrication' — renamed to taxonomy type.
        if score is not None and score < _FABRICATION_THRESHOLD:
            return ("hallucination", "llm_judge", 0.85)

        # Score low but not extreme → semantic_error.
        # Model output is syntactically valid but semantically wrong.
        # Previously 'factual_error' — renamed to taxonomy type.
        if score is not None and score < _SEMANTIC_ERROR_THRESHOLD:
            return ("semantic_error", "semantic", 0.75)

        # Score between semantic threshold and pass mark → inconclusive.
        return (None, None, None)

    def _compute_severity(self, normalized_score: Optional[float]) -> str:
        """
        Map normalized score to severity level.

        Lower score → higher severity. Validated against schema CHECK constraint.
        """
        if normalized_score is None:
            return "medium"
        if normalized_score < _SEVERITY_CRITICAL_THRESHOLD:
            return "critical"
        if normalized_score < _SEVERITY_HIGH_THRESHOLD:
            return "high"
        if normalized_score < _SEVERITY_MEDIUM_THRESHOLD:
            return "medium"
        return "low"

    def _compute_wasted_energy(self, attempt_id: int, conn) -> Optional[float]:
        """
        Compute wasted energy for a failed attempt.

        Wasted energy = total energy of the attempt's run.
        Reads from goal_attempt → run → total_energy_uj.
        Returns None if the lookup fails.
        """
        try:
            row = conn.execute(
                """
                SELECT r.total_energy_uj
                FROM goal_attempt ga
                JOIN runs r ON r.run_id = ga.run_id
                WHERE ga.attempt_id = ?
                LIMIT 1
                """,
                (attempt_id,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else None
        except Exception as exc:
            logger.warning(
                "HallucinationDetector._compute_wasted_energy failed "
                "for attempt_id=%d: %s",
                attempt_id,
                exc,
            )
            return None

    def _compute_semantic_similarity(
        self,
        actual: str,
        expected: str,
    ) -> Optional[float]:
        """
        Compute semantic similarity between actual and expected.

        Used as a secondary signal stored in hallucination_events.
        Uses token overlap (no heavy dependency here — the semantic
        scorer is used for scoring; this is just a stored signal).
        Returns None on any error.
        """
        try:
            if not actual or not expected:
                return None
            actual_tokens = set(actual.lower().split())
            expected_tokens = set(expected.lower().split())
            union = actual_tokens | expected_tokens
            if not union:
                return None
            intersection = actual_tokens & expected_tokens
            return len(intersection) / len(union)
        except Exception:
            return None
