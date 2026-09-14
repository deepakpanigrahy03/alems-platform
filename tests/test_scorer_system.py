"""
================================================================================
TEST SUITE — 8.5C Scorer Registry and Quality Judge
================================================================================

Tests for ScorerABC, ScorerRegistry, ExactMatchScorer, SemanticScorer,
QualityJudge reconciliation logic, and HallucinationDetector.

Run with:
    python3 -m pytest tests/test_scorer_system.py -v

No hardware required — all tests use in-memory SQLite and mock LLM calls.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import sqlite3
from typing import Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.registry import ScorerRegistry, DuplicateScorerError, NoScorerError
from core.execution.scorers.exact_match import ExactMatchScorer
from core.execution.scorers.semantic import SemanticScorer
from core.execution.quality_judge import QualityJudge, JudgmentResult, ACCEPTANCE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    """In-memory DB with minimal tables for quality judge tests."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE runs (
            run_id INTEGER PRIMARY KEY,
            total_energy_uj INTEGER
        );
        CREATE TABLE goal_execution (
            goal_id INTEGER PRIMARY KEY,
            exp_id INTEGER,
            workflow_type TEXT
        );
        CREATE TABLE goal_attempt (
            attempt_id INTEGER PRIMARY KEY,
            goal_id INTEGER,
            run_id INTEGER,
            outcome TEXT,
            actual_output TEXT,
            normalized_score REAL,
            pass_fail INTEGER
        );
        CREATE TABLE task_quality_config (
            config_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_category TEXT NOT NULL UNIQUE,
            metric_type TEXT NOT NULL,
            judge_method TEXT NOT NULL,
            threshold REAL NOT NULL DEFAULT 0.80,
            dual_judge INTEGER NOT NULL DEFAULT 0,
            n_judges INTEGER NOT NULL DEFAULT 1,
            judge_model_set TEXT,
            rubric TEXT,
            success_threshold REAL
        );
        CREATE TABLE output_quality (
            quality_id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            goal_id INTEGER NOT NULL,
            task_id TEXT,
            task_category TEXT,
            metric_type TEXT NOT NULL,
            raw_score REAL,
            normalized_score REAL,
            pass_fail INTEGER,
            judge_method TEXT NOT NULL,
            judge_count INTEGER NOT NULL DEFAULT 1,
            agreement_score REAL,
            score_method TEXT,
            expected_output TEXT,
            actual_output TEXT,
            energy_uj_at_judgment INTEGER,
            manual_reviewed INTEGER NOT NULL DEFAULT 0,
            judged_at TEXT NOT NULL
        );
        CREATE TABLE output_quality_judges (
            judge_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            quality_id INTEGER NOT NULL,
            attempt_id INTEGER NOT NULL,
            goal_id INTEGER NOT NULL,
            judge_model TEXT NOT NULL,
            judge_provider TEXT,
            judge_version TEXT,
            judge_temperature REAL,
            judge_score REAL NOT NULL,
            judge_confidence REAL,
            judge_prompt_hash TEXT,
            judge_reasoning TEXT,
            judged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE hallucination_events (
            hallucination_id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            goal_id INTEGER NOT NULL,
            hallucination_type TEXT NOT NULL,
            detection_method TEXT NOT NULL,
            detection_confidence REAL,
            semantic_similarity REAL,
            severity TEXT,
            wasted_energy_uj_real REAL,
            interaction_id INTEGER,
            orchestration_event_id INTEGER,
            detected_at TEXT NOT NULL
        );
        INSERT INTO runs VALUES (1, 54480000);
        INSERT INTO goal_execution VALUES (1, 1, 'agentic');
        INSERT INTO goal_attempt
            (attempt_id, goal_id, run_id, outcome, actual_output, normalized_score, pass_fail)
            VALUES (1, 1, 1, 'success', 'twelve', NULL, NULL);
        INSERT INTO task_quality_config
            (task_category, metric_type, judge_method, threshold, dual_judge, n_judges)
            VALUES ('qa', 'binary', 'exact_match', 1.0, 0, 1);
    """)
    return conn


# ---------------------------------------------------------------------------
# ScorerABC contract tests
# ---------------------------------------------------------------------------

class _ConcreteScorer(ScorerABC):
    SCORER_TYPE = "test_scorer"
    METRIC_TYPES = ("binary",)

    def score(self, actual, expected, judge_model=None, rubric=None):
        return (1.0, 1.0, "test ok")


class TestScorerABC:

    def test_concrete_scorer_instantiates(self):
        scorer = _ConcreteScorer()
        assert scorer.SCORER_TYPE == "test_scorer"
        assert scorer.is_available() is True

    def test_abstract_scorer_cannot_instantiate(self):
        class Incomplete(ScorerABC):
            SCORER_TYPE = "incomplete"
            METRIC_TYPES = ()
        with pytest.raises(TypeError):
            Incomplete()

    def test_score_returns_tuple(self):
        scorer = _ConcreteScorer()
        result = scorer.score("actual", "expected")
        assert len(result) == 3
        score, conf, reason = result
        assert 0.0 <= score <= 1.0
        assert 0.0 <= conf <= 1.0
        assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# ScorerRegistry tests
# ---------------------------------------------------------------------------

class TestScorerRegistry:

    def test_register_and_get(self):
        reg = ScorerRegistry()
        reg.register(_ConcreteScorer)
        scorer = reg.get("test_scorer")
        assert scorer.SCORER_TYPE == "test_scorer"

    def test_duplicate_raises(self):
        reg = ScorerRegistry()
        reg.register(_ConcreteScorer)
        with pytest.raises(DuplicateScorerError):
            reg.register(_ConcreteScorer)

    def test_unknown_raises(self):
        reg = ScorerRegistry()
        with pytest.raises(NoScorerError):
            reg.get("nonexistent_scorer")

    def test_get_all_returns_registered(self):
        reg = ScorerRegistry()
        reg.register(_ConcreteScorer)
        all_scorers = reg.get_all()
        assert "test_scorer" in all_scorers

    def test_is_empty_before_registration(self):
        reg = ScorerRegistry()
        assert reg.is_empty() is True

    def test_not_empty_after_registration(self):
        reg = ScorerRegistry()
        reg.register(_ConcreteScorer)
        assert reg.is_empty() is False


# ---------------------------------------------------------------------------
# ExactMatchScorer tests
# ---------------------------------------------------------------------------

class TestExactMatchScorer:

    def test_exact_match_returns_1(self):
        scorer = ExactMatchScorer()
        score, conf, reason = scorer.score("twelve", "twelve")
        assert score == 1.0
        assert conf == 1.0
        assert "exact match" in reason

    def test_case_insensitive(self):
        scorer = ExactMatchScorer()
        score, _, _ = scorer.score("TWELVE", "twelve")
        assert score == 1.0

    def test_whitespace_tolerant(self):
        scorer = ExactMatchScorer()
        score, _, _ = scorer.score("  twelve  ", "twelve")
        assert score == 1.0

    def test_mismatch_returns_0(self):
        scorer = ExactMatchScorer()
        score, conf, reason = scorer.score("eleven", "twelve")
        assert score == 0.0
        assert conf == 1.0
        assert "no match" in reason

    def test_empty_inputs(self):
        scorer = ExactMatchScorer()
        score, _, _ = scorer.score("", "")
        assert score == 1.0  # both empty — match

    def test_scorer_type(self):
        assert ExactMatchScorer.SCORER_TYPE == "exact_match"


# ---------------------------------------------------------------------------
# SemanticScorer tests
# ---------------------------------------------------------------------------

class TestSemanticScorer:

    def test_identical_strings_score_high(self):
        scorer = SemanticScorer()
        score, conf, _ = scorer.score("the cat sat on the mat", "the cat sat on the mat")
        assert score > 0.9

    def test_unrelated_strings_score_low(self):
        scorer = SemanticScorer()
        score, conf, _ = scorer.score("banana", "quantum mechanics")
        assert score < 0.5

    def test_scorer_type(self):
        assert SemanticScorer.SCORER_TYPE == "semantic"

    def test_is_always_available(self):
        scorer = SemanticScorer()
        assert scorer.is_available() is True

    def test_empty_input_returns_zero(self):
        scorer = SemanticScorer()
        score, _, _ = scorer.score("", "something")
        assert score == 0.0


# ---------------------------------------------------------------------------
# QualityJudge reconciliation tests
# ---------------------------------------------------------------------------

class TestQualityJudgeReconciliation:

    def _judge(self):
        return QualityJudge()

    def test_single_judge_method(self):
        judge = self._judge()
        score, method = judge._reconcile([0.8], n_judges_requested=1)
        assert method == "single_judge"
        assert score == 0.8

    def test_averaged_when_within_tight_band(self):
        judge = self._judge()
        # 0.8 and 0.85 — within 0.20 of each other
        score, method = judge._reconcile([0.8, 0.85], n_judges_requested=2)
        assert method == "averaged"
        assert abs(score - 0.825) < 0.001

    def test_consensus_median_when_within_loose_band(self):
        judge = self._judge()
        # 0.5 and 0.9 — median=0.7, deviations=0.2 and 0.2
        # exactly at tight band boundary — outside tight (>0.20 fails), within loose
        # Use 0.45 and 0.9: median=0.675, dev=0.225 and 0.225 — outside tight, inside loose
        score, method = judge._reconcile([0.45, 0.9], n_judges_requested=2)
        assert method == "consensus_median"
        assert score is not None


    def test_needs_review_when_disagreement(self):
        judge = self._judge()
        # 0.05 and 0.95 — median=0.5, deviations=0.45 and 0.45 — outside loose band (0.40)
        score, method = judge._reconcile([0.05, 0.95], n_judges_requested=2)
        assert method == "needs_review"
        assert score is None

    def test_majority_median_when_one_outlier(self):
        judge = self._judge()
        # 0.8, 0.82, 0.1 — two agree, one outlier
        score, method = judge._reconcile([0.8, 0.82, 0.1], n_judges_requested=3)
        assert method == "majority_median"
        assert score > 0.5  # outlier dropped

    def test_needs_review_when_disagreement(self):
        judge = self._judge()
        # 0.05 and 0.95 — median=0.5, deviations=0.45 and 0.45 — outside loose band (0.40)
        score, method = judge._reconcile([0.05, 0.95], n_judges_requested=2)
        assert method == "needs_review"
        assert score is None

    def test_empty_scores_needs_review(self):
        judge = self._judge()
        score, method = judge._reconcile([], n_judges_requested=2)
        assert method == "needs_review"
        assert score is None


# ---------------------------------------------------------------------------
# QualityJudge integration tests (in-memory DB)
# ---------------------------------------------------------------------------

class TestQualityJudgeIntegration:

    def test_judge_writes_output_quality_row(self):
        conn = _make_db()
        judge = QualityJudge()
        result = judge.judge(
            conn=conn,
            attempt_id=1,
            goal_id=1,
            task_category="qa",
            actual_output="twelve",
            expected_output="twelve",
            energy_uj=54480000,
        )
        assert result.quality_id is not None
        row = conn.execute(
            "SELECT normalized_score, pass_fail FROM output_quality WHERE quality_id = ?",
            (result.quality_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == 1.0   # exact match
        assert row[1] == 1     # pass

    def test_judge_writes_judge_rows(self):
        conn = _make_db()
        judge = QualityJudge()
        result = judge.judge(
            conn=conn,
            attempt_id=1,
            goal_id=1,
            task_category="qa",
            actual_output="twelve",
            expected_output="twelve",
            energy_uj=54480000,
        )
        judge_rows = conn.execute(
            "SELECT judge_score FROM output_quality_judges WHERE quality_id = ?",
            (result.quality_id,),
        ).fetchall()
        assert len(judge_rows) >= 1
        assert judge_rows[0][0] == 1.0

    def test_judge_skips_unknown_category(self):
        conn = _make_db()
        judge = QualityJudge()
        result = judge.judge(
            conn=conn,
            attempt_id=1,
            goal_id=1,
            task_category="nonexistent_category",
            actual_output="answer",
            expected_output="answer",
            energy_uj=0,
        )
        assert result.score_method == "needs_review"

    def test_pass_fail_set_correctly_for_fail(self):
        conn = _make_db()
        judge = QualityJudge()
        result = judge.judge(
            conn=conn,
            attempt_id=1,
            goal_id=1,
            task_category="qa",
            actual_output="wrong answer",
            expected_output="twelve",
            energy_uj=0,
        )
        assert result.pass_fail == 0   # failed
        assert result.normalized_score == 0.0
