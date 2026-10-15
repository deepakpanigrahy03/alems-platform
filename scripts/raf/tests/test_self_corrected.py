"""SELF_CORRECTED outcome (coordinator decision Oct 15): valid, never rerun."""

from alems_plugin_bfcl import raf_validity as v


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Conn:
    """Fake store: attempts (attempt_id, number, outcome), injection log, checker pass."""

    def __init__(self, attempts, log, passes=None):
        self.attempts, self.log, self.passes = attempts, log, passes or {}

    def execute(self, sql, params):
        if sql.startswith("SELECT pass_fail"):
            return _Cur([(self.passes.get(params[0]),)])
        if "failure_injection_log" in sql:
            return _Cur(self.log)
        return _Cur(self.attempts)


WMS = {"policy": "SR_warm", "cls": "wrong_model_step", "phase": "tool", "ordinal": "any"}
CLEAN = {"policy": "FR_cold", "cls": "tool_error", "phase": "tool", "ordinal": 3}
FIRED_WMS = [(10, "injected", "wrong_model_step", "tool", 4)]
FIRED_CLEAN = [(10, "injected", "tool_error", "tool", 3)]


def _checks(*oks):
    return {1: {"turn_checks": [{"turn": i, "ok": ok} for i, ok in enumerate(oks)],
                "corruption": {"applied": True}}}


def test_contaminating_repaired_in_attempt_1_is_valid():
    conn = _Conn([(10, 1, "success")], FIRED_WMS)
    out = v.classify(conn, 1, WMS, {}, _checks(True, True, True, True))
    assert out["codes"] == [] and out["detail"]["self_corrected"] is True


def test_contaminating_detected_is_not_self_corrected():
    conn = _Conn([(10, 1, "failure"), (11, 2, "success")], FIRED_WMS)
    out = v.classify(conn, 1, WMS, {}, _checks(True, False))
    assert not out["detail"].get("self_corrected")


def test_contaminating_without_checks_is_not_self_corrected():
    conn = _Conn([(10, 1, "success")], FIRED_WMS)
    out = v.classify(conn, 1, WMS, {}, {1: {"turn_checks": [], "corruption": {"applied": True}}})
    assert not out["detail"].get("self_corrected") and out["codes"]


def test_clean_with_checker_pass_is_self_corrected():
    conn = _Conn([(10, 1, "success")], FIRED_CLEAN, passes={10: 1})
    out = v.classify(conn, 1, CLEAN, {}, {})
    assert out["codes"] == [] and out["detail"]["self_corrected"] is True


def test_clean_with_checker_fail_stays_invalid():
    conn = _Conn([(10, 1, "success")], FIRED_CLEAN, passes={10: 0})
    out = v.classify(conn, 1, CLEAN, {}, {})
    assert v.INJECTION_MISMATCH in out["codes"] and not out["detail"].get("self_corrected")


def test_no_firing_is_not_self_corrected():
    conn = _Conn([(10, 1, "success")], [])
    out = v.classify(conn, 1, WMS, {}, _checks(True, True))
    assert not out["detail"].get("self_corrected")
