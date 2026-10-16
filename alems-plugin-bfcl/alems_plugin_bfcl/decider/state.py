"""
Build a FailureState from the executor at the decision point.

Only named fields are copied (addendum D3): the executor itself, the task,
the goal key and the checker result never reach a decider. Field names of
the BFCL session are read defensively; a missing field yields None and is
visible in the decision record rather than raising inside the hook.
"""

from typing import Any, Dict, List, Optional, Tuple

from .base import (WORK_SOURCE_CALLS, FailureState, check_state_dict)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _last_call(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    # The last LLM call before the failure carries the context size (R5 2.1).
    return calls[-1] if calls else {}


def build_raw(executor: Any, attempt: int,
              previous: Tuple[str, ...]) -> Dict[str, Any]:
    """Plain dict of allowed inputs; validated before FailureState exists."""
    s = getattr(executor, "_bfcl_session", None)
    f = dict(getattr(s, "failure", None) or {})
    calls = list(getattr(s, "calls", None) or [])
    last = _last_call(calls)
    starts = tuple(_int(x) for x in (f.get("turn_starts") or ()))
    return {
        # cls is the failure class; kind is only where it happened (llm, tool).
        "failure_class": str(f.get("cls") or "unknown"),
        "attempt": attempt,
        # Calls done is the work measure (x* in calls is primary, R5 D5):
        # llm_ordinal at the failure point, else the session's call list.
        "calls_done": _int(f.get("llm_ordinal"), len(calls)),
        "tokens_context": last.get("prompt_tokens"),
        "tokens_cached": last.get("cached_tokens"),
        # Attempt 1 joules live only in execute_goal's local state (verified
        # Oct 16), never on the executor, so the fallback is permanent here.
        "joules_done": None,
        "work_source": WORK_SOURCE_CALLS,
        "turns_done": _int(f.get("turn")),
        "turn_starts": starts,
        "previous_options": tuple(previous),
        "error_text": str(f.get("error") or f.get("message") or "")[:2000],
        "last_turns": _last_turns(f),
    }


def _last_turns(f: Dict[str, Any]) -> Tuple[str, ...]:
    """Last user turn and last two agent messages, when the record holds messages."""
    msgs = [m for m in (f.get("messages") or []) if isinstance(m, dict)]
    users = [m for m in msgs if m.get("role") == "user"]
    agents = [m for m in msgs if m.get("role") == "assistant"]
    pick = users[-1:] + agents[-2:]
    return tuple(str(m.get("content") or "")[:1000] for m in pick)


def build_state(executor: Any, attempt: int,
                previous: Tuple[str, ...]) -> FailureState:
    raw = build_raw(executor, attempt, previous)
    check_state_dict(raw)              # D3 and D5 guard, cheap
    return FailureState(**raw)
