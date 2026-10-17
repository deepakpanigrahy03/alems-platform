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
    CORRUPTION_NOT_APPLIED  contaminating class planned, no candidate leaf
    DETECTION_MISSED        contaminating class fired, no failed turn check
                            in attempt 1
    FALSE_DETECTION         a turn check failed in a goal without a
                            contaminating class (B0, clean, persistent)
Persistent classes must fire once in attempt 1 and once in attempt 2.
SELF_CORRECTED (detail flag, valid, never rerun): the injection fired once in
attempt 1, attempt 1 completed with a correct final state (all turn checks
passed for contaminating classes; checker pass otherwise), and no recovery
attempt ran (coordinator decision Oct 15).
NEGATIVE_DYN is an analysis flag computed in S7, not here.
"""

from typing import Any, Dict, List, Optional

INJECTION_MISMATCH = "INJECTION_MISMATCH"
INJECTION_NOT_REACHED = "INJECTION_NOT_REACHED"
PRE_INJECTION_FAILURE = "PRE_INJECTION_FAILURE"
CORRUPTION_NOT_APPLIED = "CORRUPTION_NOT_APPLIED"
DETECTION_MISSED = "DETECTION_MISSED"
FALSE_DETECTION = "FALSE_DETECTION"
B0_FAILED = "B0_FAILED"                  # A5: B0 without a successful attempt
RB_NOT_APPLICABLE = "RB_NOT_APPLICABLE"  # reported, never invalid (spec 2.1)
CORRUPT_CLASSES = ("silent_tool_output", "wrong_model_step")
PERSIST_PREFIX = "persistent_"

_ATTEMPTS = ("SELECT attempt_id, attempt_number, outcome FROM goal_attempt "
             "WHERE goal_id=? ORDER BY attempt_number")
_LOG = ("SELECT attempt_id, status, injected_type, target_phase, target_step "
        "FROM failure_injection_log WHERE goal_id=?")
_PASS = "SELECT pass_fail FROM goal_attempt WHERE attempt_id=?"
SELF_CORRECTED = "self_corrected"        # detail key; an outcome, never a code
ABANDONED = "abandoned"                  # detail key (R6): attempt number abandoned at


def _rows(conn: Any, sql: str, goal_id: int) -> List[tuple]:
    return list(conn.execute(sql, (goal_id,)).fetchall())


def classify(conn: Any, goal_id: int, plan: Dict[str, Any],
             prepare: Optional[Dict[int, Dict[str, Any]]] = None,
             attempt_meta: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Return {"codes": [...], "detail": {...}}; empty codes means valid.

    attempt_meta maps attempt number to the adapter's injection dict
    (corruption, turn_checks). Absent for pilot goals: no new codes then,
    except DETECTION_MISSED for a contaminating plan, which cannot be valid
    without proof of detection.
    """
    attempts = _rows(conn, _ATTEMPTS, goal_id)
    log = _rows(conn, _LOG, goal_id)
    codes = _prepare_codes(prepare or {})
    detail = {"attempts": len(attempts)}
    # R6: a controller abandon is an outcome (failed goal), never an invalid code.
    abandoned_at = next((a for a in sorted(prepare or {})
                         if (prepare or {})[a].get("abandon")), None)
    if abandoned_at is not None:
        detail[ABANDONED] = abandoned_at
    detail["rb_not_applicable"] = any(
        RB_NOT_APPLICABLE in (p.get("validity") or []) for p in (prepare or {}).values())
    codes += _contamination_codes(plan, attempt_meta or {}, detail)
    if detail.get("not_applicable"):
        return {"codes": codes, "detail": detail}
    if plan["policy"] == "B0":
        if attempts and not any(a[2] == "success" for a in attempts):
            codes.append(B0_FAILED)
        return {"codes": codes, "detail": detail}
    a1 = next((a for a in attempts if a[1] == 1), None)
    a2 = next((a for a in attempts if a[1] == 2), None)
    fired = {a[0]: [r for r in log if r[0] == a[0] and r[1] == "injected"]
             for a in attempts}
    f1 = fired.get(a1[0], []) if a1 else []
    f2 = fired.get(a2[0], []) if a2 else []
    detail.update({"fired_a1": len(f1), "fired_a2": len(f2)})
    if _self_corrected(conn, plan, a1, a2, f1, attempt_meta or {}):
        # The agent repaired the injected fault inside attempt 1: valid outcome.
        detail[SELF_CORRECTED] = True
        return {"codes": [c for c in codes if c != DETECTION_MISSED], "detail": detail}
    codes += _injection_codes(a1, a2, f1, f2, log, plan, detail)
    return {"codes": codes, "detail": detail}


def _self_corrected(conn: Any, plan: Dict[str, Any], a1, a2, f1,
                    meta: Dict[int, Dict[str, Any]]) -> bool:
    """Injected once in attempt 1, attempt 1 correct, no recovery attempt."""
    if a1 is None or a2 is not None or len(f1) != 1 or a1[2] != "success":
        return False
    if plan.get("cls") in CORRUPT_CLASSES:
        checks = (meta.get(1) or {}).get("turn_checks") or []
        return bool(checks) and all(c.get("ok") for c in checks)
    row = conn.execute(_PASS, (a1[0],)).fetchone()
    return bool(row) and row[0] == 1


def _prepare_codes(prepare: Dict[int, Dict[str, Any]]) -> List[str]:
    """Union of PreAttempt validity codes across attempts, order kept."""
    out = []
    for attempt in sorted(prepare):
        for code in prepare[attempt].get("validity", []):
            if code not in out and code != RB_NOT_APPLICABLE:
                out.append(code)
    return out


def _contamination_codes(plan: Dict[str, Any], meta: Dict[int, Dict[str, Any]],
                         detail: Dict[str, Any]) -> List[str]:
    """Spec R1 4.4 codes from the per attempt corruption and check records."""
    cls = None if plan["policy"] == "B0" else plan.get("cls")
    failed = {a: [c["turn"] for c in (m.get("turn_checks") or []) if not c["ok"]]
              for a, m in meta.items()}
    detail["detected_turns"] = failed
    if cls in CORRUPT_CLASSES:
        corr = (meta.get(1) or {}).get("corruption") or {}
        if corr.get("applied") is False:
            if corr.get("reason") == "not_accepted":
                # No accepted wrong step exists for this call: not applicable,
                # counted and reported, never rerun (coordinator Oct 16).
                detail["not_applicable"] = True
                return []
            return [CORRUPTION_NOT_APPLIED]
        return [] if failed.get(1) else [DETECTION_MISSED]
    return [FALSE_DETECTION] if any(failed.values()) else []


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
    want_a2 = 1 if str(plan["cls"]).startswith(PERSIST_PREFIX) else 0
    if detail.get(ABANDONED) == 2:
        want_a2 = 0          # attempt 2 never ran, so it cannot have fired
    if len(f1) != 1 or len(f2) != want_a2 or a2 is None:
        return [INJECTION_MISMATCH]
    if want_a2 and tuple(f2[0][2:5]) != (plan["cls"], plan["phase"], plan["ordinal"]):
        return [INJECTION_MISMATCH]
    _, _, itype, iphase, istep = f1[0]
    detail.update({"fired_type": itype, "fired_phase": iphase, "fired_step": istep})
    # ordinal "any": the rule targets a tool name, the step is wherever it ran.
    step = "any" if plan["ordinal"] == "any" else istep
    if (itype, iphase, step) != (plan["cls"], plan["phase"], plan["ordinal"]):
        return [INJECTION_MISMATCH]
    return []
