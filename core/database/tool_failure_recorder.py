"""
tool_failure_recorder.py — Single owner of all inserts to tool_failure_events.

Extracted from harness.py to keep that file clean and to give the recorder
a single place to enforce invariants (attempt_id and goal_id must be known
before recording — orphan rows are never created).

wasted_energy_uj is intentionally NULL at insert time.
The energy_attribution ETL populates it async after the run completes.

SPEC 8.6-A1: failure_type validation is now taxonomy-driven.
VALID_FAILURE_TYPES is loaded from failure_taxonomy table at first use.
This means new failure types added via INSERT into failure_taxonomy are
immediately accepted by the recorder — zero code changes required.
The DB CHECK constraint on failure_type is intentionally NOT used for
enforcement; application-layer validation here is the enforcement point.
failure_phase validation remains a hardcoded frozenset because phases are
structural pipeline invariants, not domain vocabulary (P1).
"""

import logging
from typing import Optional, FrozenSet

logger = logging.getLogger(__name__)

# _taxonomy_cache: loaded once per process from failure_taxonomy table.
# None means not yet loaded. Empty frozenset means table was empty or missing.
# Never mutated after first load — process restart picks up new taxonomy rows.
_taxonomy_cache: Optional[FrozenSet[str]] = None

# Fallback hardcoded set used when failure_taxonomy table does not yet exist
# (machines that have not run v098 migration yet). Matches the old CHECK
# constraint values so behaviour is identical to pre-A1 on those machines.
_FALLBACK_FAILURE_TYPES = frozenset({
    "timeout", "api_error", "malformed_input", "malformed_output",
    "rate_limit", "auth_error", "not_found", "other",
    # Paper 8 types — accepted immediately once taxonomy is loaded.
    # Listed here so they work even on machines without v098.
    "hallucination", "semantic_error", "capability_error",
    "tool_error", "json_parse", "network_error",
})

# failure_phase values are structural pipeline invariants — not domain
# vocabulary. They never grow via taxonomy INSERT, so hardcoded CHECK is
# correct here (P1: CHECK only for structural invariants).
VALID_FAILURE_PHASES = frozenset({
    "selection", "execution", "parsing", "post_processing",
})


def _load_taxonomy(conn) -> FrozenSet[str]:
    """
    Load failure_type_id values from failure_taxonomy table.

    Called once per process on first record_tool_failure() call.
    Falls back to _FALLBACK_FAILURE_TYPES if table does not exist yet
    (machine has not run v098 migration). This makes the recorder safe
    on any machine regardless of migration version.

    Returns:
        FrozenSet of valid failure_type_id strings.
    """
    try:
        rows = conn.execute(
            "SELECT failure_type_id FROM failure_taxonomy"
        ).fetchall()
        if not rows:
            # Table exists but is empty — use fallback until seeded.
            logger.warning(
                "_load_taxonomy: failure_taxonomy is empty — using fallback set"
            )
            return _FALLBACK_FAILURE_TYPES
        loaded = frozenset(r[0] for r in rows)
        logger.debug(
            "_load_taxonomy: loaded %d failure types from taxonomy", len(loaded)
        )
        return loaded
    except Exception:
        # Table does not exist yet (pre-v098 machine) — use fallback silently.
        # Not a warning — expected on machines behind on migrations.
        return _FALLBACK_FAILURE_TYPES


def _get_valid_types(conn) -> FrozenSet[str]:
    """
    Return valid failure types, loading from taxonomy on first call.

    Uses module-level cache so the DB is queried at most once per process.
    Cache is intentionally not invalidated during the process lifetime —
    new taxonomy rows require a process restart to take effect, which is
    acceptable because adding a new failure type is a deployment event.
    """
    global _taxonomy_cache
    if _taxonomy_cache is None:
        _taxonomy_cache = _load_taxonomy(conn)
    return _taxonomy_cache


def record_tool_failure(
    conn,
    attempt_id: int,
    goal_id: int,
    tool_name: str,
    failure_type: str,
    failure_phase: Optional[str] = None,
    error_message: Optional[str] = None,
    retry_attempted: int = 0,
    retry_success: int = 0,
    recovery_strategy: Optional[str] = None,
    orchestration_event_id: Optional[int] = None,
) -> Optional[int]:
    """
    Insert one row into tool_failure_events.

    wasted_energy_uj is always NULL at insert — energy_attribution_etl
    populates it after the run completes (SC-4 ETL pattern).

    failure_type is validated against failure_taxonomy (loaded from DB on
    first call). Unknown types are coerced to 'tool_error' — not 'other',
    because 'other' is no longer a canonical taxonomy entry after 8.6-A1.
    The coercion is logged as a warning so callers can fix their code.

    Args:
        conn:                   SQLite connection.
        attempt_id:             goal_attempt.attempt_id — must exist.
        goal_id:                goal_execution.goal_id — must exist.
        tool_name:              Name of the failing tool.
        failure_type:           Canonical type from failure_taxonomy.
        failure_phase:          Phase in which failure occurred, or None.
        error_message:          Raw error string. Prefix 'INJECTED:' for synthetic.
        retry_attempted:        1 if a retry was attempted for this tool call.
        retry_success:          1 if the retry succeeded.
        recovery_strategy:      Recovery action taken, or None.
        orchestration_event_id: FK to orchestration_events if available.

    Returns:
        failure_id (int) on success, None on failure.
    """
    # Guard: never create orphan rows — caller must have valid ids.
    if attempt_id is None or goal_id is None:
        logger.warning(
            "record_tool_failure: skipping — attempt_id=%s goal_id=%s not set",
            attempt_id, goal_id,
        )
        return None

    # Validate failure_type against taxonomy (DB-driven, not hardcoded).
    valid_types = _get_valid_types(conn)
    if failure_type not in valid_types:
        logger.warning(
            "record_tool_failure: unrecognised failure_type=%r "
            "(not in failure_taxonomy) — coercing to 'tool_error'. "
            "Add a row to failure_taxonomy to accept new types.",
            failure_type,
        )
        # Coerce to tool_error — closest structural catch-all in taxonomy.
        # Pre-A1 code that passes 'other' lands here and gets coerced cleanly.
        failure_type = "tool_error"

    # failure_phase is a structural invariant — hardcoded check is correct.
    if failure_phase is not None and failure_phase not in VALID_FAILURE_PHASES:
        logger.warning(
            "record_tool_failure: unrecognised failure_phase=%r — setting NULL",
            failure_phase,
        )
        failure_phase = None

    try:
        cur = conn.execute(
            """
            INSERT INTO tool_failure_events (
                attempt_id, goal_id, orchestration_event_id,
                tool_name, failure_type, failure_phase,
                error_message, retry_attempted, retry_success,
                recovery_strategy,
                wasted_energy_uj
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                attempt_id, goal_id, orchestration_event_id,
                tool_name, failure_type, failure_phase,
                error_message, retry_attempted, retry_success,
                recovery_strategy,
            ),
        )
        conn.commit()
        logger.debug(
            "record_tool_failure: failure_id=%d tool=%r type=%s",
            cur.lastrowid, tool_name, failure_type,
        )
        return cur.lastrowid

    except Exception as exc:
        # Log and return None — harness must continue even if recording fails.
        logger.error("record_tool_failure: insert failed: %s", exc)
        return None
