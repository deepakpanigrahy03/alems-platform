"""
================================================================================
EXPECTATION SCHEMA  —  core/execution/expectation/schema.py
================================================================================

PURPOSE:
    Defines the Expectation dataclass parsed from a task's expectation: block.
    Validates the block at task load time so misconfigured tasks fail early.

    tasks.yaml expectation block formats:

        # Exact match
        expectation:
          scorer_type: exact
          expected_source: inline
          answer: "12"

        # Numeric with tolerance
        expectation:
          scorer_type: numeric
          expected_source: inline
          answer: "12"

        # Rubric-based LLM judge
        expectation:
          scorer_type: rubric
          expected_source: inline
          rubric:
            key_concepts: ["orchestration", "energy"]
            min_words: 100

        # Structural (tool/file/API verification)
        expectation:
          scorer_type: structural
          expected_source: inline
          conditions:
            - tool_called: write_file
            - file_exists: output.txt

        # Semantic similarity
        expectation:
          scorer_type: semantic
          expected_source: inline
          answer: "reference text to compare against"

        # No scoring
        expectation:
          scorer_type: none

VALID scorer_types: exact, numeric, rubric, structural, semantic, none
VALID expected_sources: inline, benchmark_dataset (others deferred)

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid scorer type strings — must match ScorerABC.SCORER_TYPE values.
VALID_SCORER_TYPES = frozenset({
    "exact", "numeric", "rubric", "structural", "semantic", "none"
})

# Valid expected source strings.
VALID_EXPECTED_SOURCES = frozenset({
    "inline",
    "benchmark_dataset",    # deferred — loader not implemented yet
    "computed_at_runtime",  # deferred — oracle not implemented yet
})


@dataclass
class Expectation:
    """
    Parsed expectation block from a task definition.

    scorer_type   : which scorer to use (key into ScorerRegistry).
    expected_source: where the expected value comes from.
    answer        : inline expected answer string (exact/numeric/semantic).
    rubric        : inline rubric dict (rubric scorer_type).
    conditions    : inline condition list (structural scorer_type).
    benchmark_ref : reference for benchmark_dataset source (deferred).
    raw           : original dict from tasks.yaml (for debugging).
    """

    scorer_type: str
    expected_source: str = "inline"
    answer: Optional[str] = None
    rubric: Optional[Dict] = None
    conditions: Optional[List[Dict]] = None
    benchmark_ref: Optional[Dict] = None
    raw: Dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict) -> "Expectation":
        """
        Parse an expectation block dict from tasks.yaml.

        Args:
            data: Dict from the expectation: key in a task definition.

        Returns:
            Expectation instance.

        Raises:
            ValueError: if scorer_type is missing or invalid.
        """
        if not data:
            return cls(scorer_type="none")

        scorer_type = str(data.get("scorer_type", "none")).lower()
        if scorer_type not in VALID_SCORER_TYPES:
            raise ValueError(
                f"Invalid scorer_type='{scorer_type}' in expectation block. "
                f"Valid types: {sorted(VALID_SCORER_TYPES)}"
            )

        expected_source = str(data.get("expected_source", "inline")).lower()
        if expected_source not in VALID_EXPECTED_SOURCES:
            logger.warning(
                "Unknown expected_source='%s' — defaulting to 'inline'",
                expected_source,
            )
            expected_source = "inline"

        return cls(
            scorer_type=scorer_type,
            expected_source=expected_source,
            answer=data.get("answer"),
            rubric=data.get("rubric"),
            conditions=data.get("conditions"),
            benchmark_ref=data.get("benchmark_ref"),
            raw=data,
        )

    @classmethod
    def none(cls) -> "Expectation":
        """Return a no-op expectation (task has no quality scoring)."""
        return cls(scorer_type="none")

    def get_expected_value(self) -> Any:
        """
        Return the expected value for the scorer.

        For exact/numeric/semantic: returns answer string.
        For rubric: returns rubric dict.
        For structural: returns conditions list.
        For none: returns None.
        """
        if self.scorer_type in ("exact", "numeric", "semantic"):
            return self.answer
        if self.scorer_type == "rubric":
            return self.rubric
        if self.scorer_type == "structural":
            return self.conditions
        return None

    def requires_execution_trace(self) -> bool:
        """Return True if this expectation needs the agentic execution trace."""
        return self.scorer_type == "structural"

    def is_scoreable(self) -> bool:
        """Return True if this expectation can produce a quality score."""
        if self.scorer_type == "none":
            return False
        expected = self.get_expected_value()
        return expected is not None
