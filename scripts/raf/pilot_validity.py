"""
RAF LH 01 validity classification for one goal execution (design 5.2, 5.3, 13).

Read only. Takes an open connection (the runner's own, so no new connection
is opened) and the goal's planned injection. Codes:
    INJECTION_MISMATCH      attempt 1 fired != 1, attempt 2 fired != 0,
                            fired at the wrong type, phase or ordinal,
                            or no recovery attempt exists
    INJECTION_NOT_REACHED   attempt 1 logged target_not_reached
    PRE_INJECTION_FAILURE   attempt 1 failed with no injection fired
    CACHE_RESET_FAILED,
    RESTORE_MISMATCH        taken from the PreAttempt records
NEGATIVE_DYN is an analysis flag computed in S7, not here.
"""

from typing import Any, Dict, List, Optional

INJECTION_MISMATCH = "INJECTION_MISMATCH"
INJECTION_NOT_REACHED = "INJECTION_NOT_REACHED"
PRE_INJECTION_FAILURE = "PRE_INJECTION_FAILURE"

_ATTEMPTS = ("SELECT attempt_id, attempt_number, outcome FROM goal_attempt "
             "WHERE goal_id=? ORDER BY attempt_number")
_LOG = ("SELECT attempt_id, status, injected_type, target_phase, target_step "
        "FROM failure_injection_log WHERE goal_id=?")


def _rows(conn: Any, sql: str, goal_id: int) -> List[tuple]:
    return list(conn.execute(sql, (goal_id,)).fetchall())


def classify(conn: Any, goal_id: int, plan: Dict[str, Any],
             prepare: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Return {"codes": [...], "detail": {...}}; empty codes means valid."""
    attempts = _rows(conn, _ATTEMPTS, goal_id)
    log = _rows(conn, _LOG, goal_id)
    codes = _prepare_codes(prepare or {})
    detail = {"attempts": len(attempts)}
    if plan["policy"] == "B0":
        return {"codes": codes, "detail": detail}
    a1 = next((a for a in attempts if a[1] == 1), None)
    a2 = next((a for a in attempts if a[1] == 2), None)
    fired = {a[0]: [r for r in log if r[0] == a[0] and r[1] == "injected"]
             for a in attempts}
    f1 = fired.get(a1[0], []) if a1 else []
    f2 = fired.get(a2[0], []) if a2 else []
    detail.update({"fired_a1": len(f1), "fired_a2": len(f2)})
    codes += _injection_codes(a1, a2, f1, f2, log, plan, detail)
    return {"codes": codes, "detail": detail}


def _prepare_codes(prepare: Dict[int, Dict[str, Any]]) -> List[str]:
    """Union of PreAttempt validity codes across attempts, order kept."""
    out = []
    for attempt in sorted(prepare):
        for code in prepare[attempt].get("validity", []):
            if code not in out:
                out.append(code)
    return out


def _injection_codes(a1, a2, f1, f2, log, plan, detail) -> List[str]:
    if a1 is None:
        return [INJECTION_MISMATCH]
    if not f1:
        # No firing: either the trajectory ended early or it failed on its own.
        not_reached = any(r[0] == a1[0] and r[1] == "target_not_reached" for r in log)
        if not_reached:
            return [INJECTION_NOT_REACHED]
        if a1[2] != "success":
            return [PRE_INJECTION_FAILURE]
        return [INJECTION_MISMATCH]
    if len(f1) != 1 or f2 or a2 is None:
        return [INJECTION_MISMATCH]
    _, _, itype, iphase, istep = f1[0]
    detail.update({"fired_type": itype, "fired_phase": iphase, "fired_step": istep})
    if (itype, iphase, istep) != (plan["cls"], plan["phase"], plan["ordinal"]):
        return [INJECTION_MISMATCH]
    return []
