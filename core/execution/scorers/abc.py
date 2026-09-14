"""
================================================================================
SCORER ABC  —  core/execution/scorers/abc.py
================================================================================

PURPOSE:
    Abstract base class for all A-LEMS quality scorers.
    Follows the same adapter pattern as EnergyReaderABC (35A) and
    TextGenABC (35B): one class per scorer type, registered in
    ScorerRegistry, selected by SCORER_TYPE string.

    Built-in scorers (exact_match, llm_judge, semantic) are registered
    via bootstrap.py. Community scorers register via entry_points
    (alems.scorers group) in 35G.

CONTRACT:
    score() returns (score, confidence, reasoning).
    Score is always float in [0.0, 1.0].
    Confidence reflects how reliable the score is.
    Reasoning is a human-readable explanation (used in output_quality_judges).
    score() must NEVER raise — return (0.0, 0.0, 'scorer_failed') on error.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class ScorerABC(ABC):
    """
    Abstract base class for all quality scorer adapters.

    Subclass contract:
        SCORER_TYPE: str   — stable identity string, matches judge_method
                             values in task_quality_config and output_quality.
                             Examples: "exact_match", "llm_judge", "semantic".
        METRIC_TYPES: tuple — which metric_type values this scorer supports.
                              Used for validation at registration time.

    The score() method is the sole integration point.
    QualityJudge calls score() for each judge in the N-judge loop.
    The scorer never writes to the database.
    """

    # Subclasses must declare these as class attributes.
    SCORER_TYPE: str = ""
    METRIC_TYPES: Tuple[str, ...] = ()

    @abstractmethod
    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score actual output against expected output.

        Args:
            actual   : The LLM's actual output for this attempt.
            expected : The reference answer or rubric key.
            judge_model: Model identifier for LLM-based scorers.
                         Ignored by deterministic scorers (exact_match, semantic).
            rubric   : Optional scoring rubric dict for scalar LLM judge tasks.
                       None for binary and testsuite tasks.

        Returns:
            Tuple of (score, confidence, reasoning):
                score     : float in [0.0, 1.0]. 1.0 = perfect match.
                confidence: float in [0.0, 1.0]. How reliable is this score?
                            1.0 for deterministic methods (exact_match).
                            0.0 when scorer failed (API error, parse error).
                reasoning : Human-readable explanation of the score.
                            Stored in output_quality_judges.judge_reasoning.
                            "scorer_failed" when score could not be computed.

        Must never raise. Return (0.0, 0.0, "scorer_failed") on any error.
        """

    def is_available(self) -> bool:
        """
        Return True if this scorer can operate in the current environment.

        Override for scorers with optional dependencies (e.g. sentence-transformers
        for semantic similarity, or an API key for LLM judge).
        Default returns True — deterministic scorers are always available.
        """
        return True

    def get_name(self) -> str:
        """
        Return human-readable scorer name for logging.

        Default uses SCORER_TYPE. Override for more descriptive names.
        """
        return self.SCORER_TYPE
