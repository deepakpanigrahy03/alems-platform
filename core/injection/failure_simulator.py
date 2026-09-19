"""
================================================================================
failure_simulator.py — Injectable Failure Behavior Dispatch
================================================================================

Maps injectable failure_taxonomy types to concrete runtime behavior.
Called by ScenarioInjector dispatch points in agentic.py and
goal_execution_manager.py when should_inject() returns (True, failure_type_id).

ERROR STRING CONTRACT:
    All injected failures use structured prefix: INJECTED[failure_type_id]: description
    This allows _detect_error_type() in agentic.py to extract the type directly
    via regex — no hardcoded prose pattern matching required.
    Adding a new injectable type: add case here + row in failure_taxonomy.
    Zero other code changes needed.

NON-INJECTABLE TYPES (reasoning domain):
    hallucination, semantic_error, capability_error
    These are DETECTED from real LLM runs via HallucinationDetector.

INJECTABLE TYPES (execution, communication, validation domains):
    tool_error, timeout, api_error, network_error, rate_limit,
    malformed_output, json_parse, not_found, auth_error

ARCHITECTURAL CONTRACT:
    _dispatch_tool() comment says "never raises".
    simulate_tool_failure() MUST always return ToolResult — never raise.
    timeout fires post-harness via simulate_post_harness_failure().

Author: Deepak Panigrahy
SPEC: 8.6-A2, A2.10
================================================================================
"""

import logging
from typing import Optional

from core.execution.tools.real_tools import ToolResult

logger = logging.getLogger(__name__)

NON_INJECTABLE_TYPES = frozenset({
    "hallucination",
    "semantic_error",
    "capability_error",
})

# Structured injection prefix — classifier extracts type_id via regex.
# Format: INJECTED[failure_type_id]: human readable description
_PREFIX = "INJECTED[{type_id}]:"


def _injected_error(type_id: str, description: str) -> str:
    """Build structured injection error string."""
    return f"{_PREFIX.format(type_id=type_id)} {description}"


def simulate_tool_failure(
    failure_type_id: str,
    tool_name: str,
) -> Optional[ToolResult]:
    """
    Simulate a tool-level failure for the given taxonomy type.

    Always returns ToolResult — never raises (preserves _dispatch_tool contract).
    Error strings use INJECTED[type_id]: prefix for classifier extraction.

    Raises:
        ValueError: for non-injectable reasoning-domain types only.
    """
    if failure_type_id in NON_INJECTABLE_TYPES:
        raise ValueError(
            f"simulate_tool_failure: {failure_type_id!r} is a reasoning-domain type "
            f"and cannot be injected."
        )

    if failure_type_id == "tool_error":
        logger.info("INJECT tool_error on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("tool_error", f"tool invocation failed for {tool_name}"),
        )

    if failure_type_id == "timeout":
        logger.info("INJECT timeout-as-tool-failure on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("timeout", f"tool timeout for {tool_name}"),
        )

    if failure_type_id == "api_error":
        logger.info("INJECT api_error on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("api_error", f"API error 500 for {tool_name}"),
        )

    if failure_type_id == "network_error":
        logger.info("INJECT network_error on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("network_error", f"network failure for {tool_name}"),
        )

    if failure_type_id == "rate_limit":
        logger.info("INJECT rate_limit on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("rate_limit", f"rate limit 429 for {tool_name}"),
        )

    if failure_type_id == "malformed_output":
        logger.info("INJECT malformed_output on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("malformed_output", f"malformed output from {tool_name}"),
        )

    if failure_type_id == "json_parse":
        logger.info("INJECT json_parse on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("json_parse", f"JSON parse failed for {tool_name}"),
        )

    if failure_type_id == "not_found":
        logger.info("INJECT not_found on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("not_found", f"resource not found 404 for {tool_name}"),
        )

    if failure_type_id == "auth_error":
        logger.info("INJECT auth_error on %s", tool_name)
        return ToolResult(
            success=False, result=None, tool_name=tool_name, duration_ns=0,
            error=_injected_error("auth_error", f"authentication failed 401 for {tool_name}"),
        )

    # Unknown type catch-all — new taxonomy types work immediately via this path.
    logger.warning(
        "simulate_tool_failure: unknown type=%r — using catch-all. "
        "Add a case here for full support.",
        failure_type_id,
    )
    return ToolResult(
        success=False, result=None, tool_name=tool_name, duration_ns=0,
        error=_injected_error(failure_type_id, f"simulated failure for {tool_name}"),
    )


def simulate_post_harness_failure(failure_type_id: str) -> dict:
    """
    Simulate a post-harness failure returning a result["execution"] fragment.
    Error strings use INJECTED[type_id]: prefix for classifier extraction.
    """
    if failure_type_id in NON_INJECTABLE_TYPES:
        raise ValueError(
            f"simulate_post_harness_failure: {failure_type_id!r} cannot be injected."
        )

    if failure_type_id == "timeout":
        return {
            "status":        "failure",
            "error_type":    "timeout",
            "completed":     True,
            "error_message": _injected_error("timeout", "simulated timeout"),
            "failed_steps":  0,
            "total_steps":   0,
        }

    if failure_type_id == "rate_limit":
        return {
            "status":        "failure",
            "error_type":    "rate_limit",
            "completed":     False,
            "error_message": _injected_error("rate_limit", "rate limit 429"),
            "failed_steps":  0,
            "total_steps":   0,
        }

    if failure_type_id == "api_error":
        return {
            "status":        "failure",
            "error_type":    "api_error",
            "completed":     False,
            "error_message": _injected_error("api_error", "API error 500"),
            "failed_steps":  0,
            "total_steps":   0,
        }

    if failure_type_id == "network_error":
        return {
            "status":        "failure",
            "error_type":    "network_error",
            "completed":     False,
            "error_message": _injected_error("network_error", "network failure"),
            "failed_steps":  0,
            "total_steps":   0,
        }

    return {
        "status":        "failure",
        "error_type":    failure_type_id,
        "completed":     False,
        "error_message": _injected_error(failure_type_id, f"simulated {failure_type_id}"),
        "failed_steps":  0,
        "total_steps":   0,
    }
