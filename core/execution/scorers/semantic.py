"""
================================================================================
SEMANTIC SCORER  —  core/execution/scorers/semantic.py
================================================================================

PURPOSE:
    Semantic similarity scorer. Uses sentence-transformers if available,
    falls back to token overlap ratio if not installed. Confidence reflects
    which method was used: 0.85 for embeddings, 0.60 for token overlap.

    SCORER_TYPE matches the judge_method CHECK constraint in output_quality:
    'semantic' (NOT 'semantic_similarity' — the DB CHECK uses 'semantic').

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional, Tuple

from core.execution.scorers.abc import ScorerABC

logger = logging.getLogger(__name__)

# Confidence levels for each method.
# Embeddings are more reliable than token overlap.
_EMBEDDING_CONFIDENCE = 0.85
_TOKEN_OVERLAP_CONFIDENCE = 0.60


_SHARED_MODEL = None
_SHARED_UTIL = None
_USE_EMBEDDINGS = False


def _try_load_shared_model():
    global _SHARED_MODEL, _SHARED_UTIL, _USE_EMBEDDINGS
    try:
        from sentence_transformers import SentenceTransformer, util
        _SHARED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _SHARED_UTIL = util
        _USE_EMBEDDINGS = True
        logger.debug("SemanticScorer: sentence-transformers loaded (singleton)")
    except ImportError:
        logger.debug("SemanticScorer: sentence-transformers not available — using token overlap")


class SemanticScorer(ScorerABC):
    """
    Semantic similarity scorer.

    Primary: cosine similarity between sentence-transformer embeddings.
    Fallback: Jaccard token overlap ratio when sentence-transformers
              is not installed.

    Use for: translation tasks, summarization tasks, creative writing —
    anywhere exact match is too strict and LLM judging is too expensive.

    Confidence: 0.85 (embedding) or 0.60 (token overlap fallback).
    judge_model and rubric are ignored — this is an embedding method.
    """

    SCORER_TYPE = "semantic"
    METRIC_TYPES = ("scalar",)

    def __init__(self) -> None:
        # Load shared model on first instantiation only.
        # Subsequent instances reuse the module-level singleton.
        if not _USE_EMBEDDINGS and _SHARED_MODEL is None:
            _try_load_shared_model()

    def is_available(self) -> bool:
        """Always available — token overlap fallback requires no dependencies."""
        return True

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score by semantic similarity.

        Uses sentence-transformer embeddings when available, token overlap
        otherwise. Returns score in [0.0, 1.0].

        Args:
            actual    : LLM's output string.
            expected  : Reference answer string.
            judge_model: Ignored — embedding method needs no judge model.
            rubric    : Ignored.

        Returns:
            (score, confidence, reasoning) tuple.
        """
        try:
            if not actual or not expected:
                return (0.0, 1.0, "empty input")

            if _USE_EMBEDDINGS:
                return self._score_embeddings(actual, expected)
            return self._score_token_overlap(actual, expected)

        except Exception as exc:
            logger.error("SemanticScorer.score failed: %s", exc)
            return (0.0, 0.0, "scorer_failed")

    def _score_embeddings(self, actual: str, expected: str) -> Tuple[float, float, str]:
        """
        Compute cosine similarity between sentence embeddings.
        Clips to [0.0, 1.0] — cosine can return slightly negative values
        for very dissimilar sentences.
        """
        try:
            embeddings = _SHARED_MODEL.encode([actual, expected], convert_to_tensor=True)
            # cosine_similarity returns a tensor — extract scalar.
            cosine = float(_SHARED_UTIL.cos_sim(embeddings[0], embeddings[1]))
            score = max(0.0, min(1.0, cosine))
            reasoning = f"embedding cosine_similarity={cosine:.4f}"
            return (score, _EMBEDDING_CONFIDENCE, reasoning)
        except Exception as exc:
            logger.warning("embedding scoring failed, falling back to token overlap: %s", exc)
            return self._score_token_overlap(actual, expected)

    def _score_token_overlap(self, actual: str, expected: str) -> Tuple[float, float, str]:
        """
        Jaccard token overlap as fallback.
        Score = |intersection| / |union| of token sets.
        """
        actual_tokens = set(actual.lower().split())
        expected_tokens = set(expected.lower().split())

        if not actual_tokens and not expected_tokens:
            return (1.0, _TOKEN_OVERLAP_CONFIDENCE, "both empty")

        intersection = actual_tokens & expected_tokens
        union = actual_tokens | expected_tokens
        score = len(intersection) / len(union) if union else 0.0
        reasoning = (
            f"token_overlap jaccard={score:.4f} "
            f"(intersection={len(intersection)} union={len(union)})"
        )
        return (score, _TOKEN_OVERLAP_CONFIDENCE, reasoning)
