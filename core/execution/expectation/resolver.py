"""
================================================================================
EXPECTED SOURCE RESOLVER  —  core/execution/expectation/resolver.py
================================================================================

PURPOSE:
    Resolves the expected value from its source.
    Phase 1: inline source only.
    Deferred: benchmark_dataset (GSM8K file loader), computed_at_runtime (oracle).

    The resolver separates WHERE the expected value comes from (source)
    from HOW correctness is judged (scorer_type). This means adding a new
    data source (a new benchmark dataset) is a resolver change only —
    the scorer registry is unaffected.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Any, Optional

from core.execution.expectation.schema import Expectation

logger = logging.getLogger(__name__)


class ExpectedSourceResolver:
    """
    Resolves the expected value for a scoring call.

    Phase 1 supports inline source only. Benchmark dataset and
    computed_at_runtime sources raise NotImplementedError with a clear
    message so researchers know these are planned but not yet available.
    """

    def resolve(self, expectation: Expectation) -> Optional[Any]:
        """
        Resolve the expected value from its source.

        Args:
            expectation: Parsed Expectation from the task definition.

        Returns:
            The expected value ready to pass to the scorer.
            None if resolution fails or scorer_type is none.

        Raises:
            NotImplementedError: for unimplemented sources.
        """
        if not expectation.is_scoreable():
            return None

        source = expectation.expected_source

        if source == "inline":
            return self._resolve_inline(expectation)

        if source == "benchmark_dataset":
            raise NotImplementedError(
                "expected_source='benchmark_dataset' is not yet implemented. "
                "Add the benchmark_ref to the task and load the dataset "
                "via a BenchmarkLoader (planned for a future chunk). "
                "For now, use expected_source='inline' with answer: from the dataset."
            )

        if source == "computed_at_runtime":
            raise NotImplementedError(
                "expected_source='computed_at_runtime' (oracle queries) "
                "is not yet implemented. "
                "Use expected_source='inline' for tasks with known expected outputs."
            )

        logger.warning(
            "ExpectedSourceResolver: unknown source='%s' — returning None",
            source,
        )
        return None

    def _resolve_inline(self, expectation: Expectation) -> Optional[Any]:
        """
        Return the inline expected value from the expectation block.

        For exact/numeric/semantic: returns answer string.
        For rubric: returns rubric dict.
        For structural: returns conditions list.
        """
        value = expectation.get_expected_value()
        if value is None:
            logger.warning(
                "ExpectedSourceResolver: scorer_type='%s' with expected_source='inline' "
                "has no expected value. Add 'answer:', 'rubric:', or 'conditions:' "
                "to the expectation block in tasks.yaml.",
                expectation.scorer_type,
            )
        return value
