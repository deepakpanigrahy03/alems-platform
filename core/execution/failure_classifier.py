"""
failure_classifier.py — Maps exceptions and run outcomes to canonical failure types.

Called by RetryCoordinator after each failed attempt to determine:
  1. What type of failure occurred
  2. Whether it is retryable under the active policy

Canonical types must stay in sync with goal_attempt.failure_type column.
Never raises — always returns a valid string from FAILURE_TYPES.
"""

import logging

logger = logging.getLogger(__name__)

# Canonical failure type set — mirrors failure_taxonomy.failure_type_id values.
# SPEC 8.6-A1: taxonomy is the source of truth. This set is a runtime cache
# for fast validation without a DB query. Keep in sync with s014+s015 seed.
# Reasoning-domain types (hallucination, semantic_error, capability_error)
# are detected by HallucinationDetector — not produced by this classifier.
VALID_FAILURE_TYPES = frozenset({
    # execution domain
    "tool_error", "timeout", "malformed_input", "crashed",
    # communication domain
    "api_error", "network_error", "rate_limit", "auth_error", "not_found",
    # validation domain
    "malformed_output", "json_parse",
    # reasoning domain — detected only, never classified from exceptions
    "hallucination", "semantic_error", "capability_error",
})

# Legacy type → taxonomy type mapping.
# Pre-A1 code paths may still write old type strings into error_type.
# This map normalises them at classification time without touching the DB.
_legacy_type_map = {
    "wrong_answer":     "semantic_error",
    "context_overflow": "capability_error",
    "other":            "tool_error",
    "hallucination":    "hallucination",   # pass through — detector handles it
}

# Score below which a completed run is classified as semantic_error.
# Previously 'wrong_answer' — renamed to taxonomy type.
# Tied to output_quality_normalization_v1 — bump version if threshold changes.
SEMANTIC_ERROR_THRESHOLD = 0.5


class FailureClassifier:
    """
    Maps exceptions and harness result dicts to canonical failure type strings.

    Priority order: exception type > run_result fields > 'crashed' fallback.
    This ordering ensures infrastructure failures are never masked by quality checks.
    """

    def classify(
        self,
        exception: Exception = None,
        run_result: dict = None,
    ) -> str:
        """
        Classify a failure into one canonical type.

        Args:
            exception:  Exception raised during execution, if any.
            run_result: Harness result dict, used when no exception was raised
                        but the run produced a bad outcome (e.g. wrong answer).

        Returns:
            Canonical failure type string from FAILURE_TYPES.
        """
        if exception is not None:
            return self._classify_exception(exception)

        if run_result is not None:
            return self._classify_result(run_result)

        # Both None — caller has no information; treat as crashed
        logger.warning("FailureClassifier: called with no exception and no result")
        return "crashed"

    def _classify_exception(self, exc: Exception) -> str:
        """
        Map exception type to taxonomy failure_type_id.
        Checks class name strings to avoid hard imports of provider SDKs.
        Never returns old pre-taxonomy types (context_overflow, wrong_answer, other).
        """
        exc_type = type(exc).__name__
        exc_str  = str(exc).lower()

        # Timeout family — covers stdlib, concurrent.futures, httpx, injected
        if exc_type in ("TimeoutError", "TimeoutExpired") or "Timeout" in exc_type:
            return "timeout"

        # Rate limit — provider SDKs use RateLimitError or 429-based names
        if "RateLimit" in exc_type or "rate_limit" in exc_str \
                or "429" in str(exc) or "too many requests" in exc_str:
            return "rate_limit"

        # Auth failure — 401 / credential errors
        if "Auth" in exc_type or "401" in str(exc) \
                or "authentication" in exc_str or "unauthorized" in exc_str:
            return "auth_error"

        # Context length exceeded → capability_error (taxonomy alignment)
        # LLM attempted task beyond its context capacity.
        if "ContextLength" in exc_type or "context_length" in exc_str \
                or "exceed context window" in exc_str \
                or "context window" in exc_str:
            return "capability_error"

        # Network failures — ConnectionError, DNS, etc.
        if exc_type in ("ConnectionError", "ConnectError") \
                or "network" in exc_str or "dns" in exc_str \
                or "connection refused" in exc_str:
            return "network_error"

        # Generic API error — catch remaining provider SDK errors
        if exc_type in ("APIError", "APIStatusError", "APIConnectionError"):
            return "api_error"

        # Catch-all — process/harness crash
        logger.debug(
            "FailureClassifier: unrecognised exception %s — classifying as crashed",
            exc_type,
        )
        return "crashed"

    def _classify_result(self, run_result: dict) -> str:
        """
        4-layer structured failure detection — taxonomy-aligned output.
        Layer 1: explicit tool_error flag.
        Layer 2: execution.error_type — set by agentic structured detection.
        Layer 3: execution.error_message — keyword scan.
        Layer 4: scan step results for error strings — legacy fallback.
        Never returns pre-taxonomy types (context_overflow, wrong_answer, other).
        """
        if run_result.get("tool_error"):
            return "tool_error"

        # run_result["execution"] is the full executor output dict.
        # The structured failure metadata is nested one level deeper at
        # run_result["execution"]["execution"] (set by agentic.execute()).
        _outer = run_result.get("execution", {}) or {}
        exec_dict = _outer.get("execution", _outer) or {}

        # Layer 2 — structured error_type from agentic.py (most reliable).
        # Map legacy types to taxonomy equivalents on the way through.
        error_type = exec_dict.get("error_type")
        if error_type:
            mapped = _legacy_type_map.get(error_type, error_type)
            if mapped in VALID_FAILURE_TYPES:
                return mapped

        # Layer 3 — error_message keyword scan.
        error_msg = str(exec_dict.get("error_message", "") or "")
        if error_msg:
            import re as _re
            # Injected failures carry INJECTED[type_id]: prefix — extract directly.
            _match = _re.search(r"INJECTED\[([a-z_]+)\]", error_msg)
            if _match:
                return _match.group(1)
            # Real provider error keyword scan.
            m = error_msg.lower()
            if "429" in m or "too many requests" in m or "rate_limit" in m:
                return "rate_limit"
            if "401" in m or "unauthorized" in m:
                return "auth_error"
            if "404" in m or "not found" in m:
                return "not_found"
            if "context window" in m or "exceed context" in m or "context_length" in m:
                return "capability_error"
            if "timeout" in m or "timed out" in m:
                return "timeout"
            if "connection" in m or "api error" in m:
                return "api_error"

        # Layer 4 — scan step results (legacy/fallback).
        steps = run_result.get("step_results", []) or []
        for step in steps:
            content = str(step.get("result", "") or "").lower()
            if "429" in content or "too many requests" in content:
                return "rate_limit"
            if "context window" in content or "context_length" in content:
                return "capability_error"
            if "timeout" in content or "timed out" in content:
                return "timeout"
            if "tool_error" in content or "tool failed" in content:
                return "tool_error"
            if "json parse" in content:
                return "json_parse"
            if content.startswith("error:"):
                return "api_error"

        # Quality score fallback — low score on completed run = semantic error.
        score = run_result.get("quality_score")
        if score is not None and score < SEMANTIC_ERROR_THRESHOLD:
            return "semantic_error"

        # Status fallback — failed but unclassified.
        if exec_dict.get("status") in ("failure", "failed", "partial_failure"):
            return "api_error"

        return None
 

