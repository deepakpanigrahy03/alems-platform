"""
Entry point objects for alems.harness.deciders (seam of R2: decide(attempt, executor)).

ctrl_beta and ctrl_laya are callable objects; the scheduled runner loads one
per arm and keeps it for the whole process, so the posterior learns across
goals. Construction is lazy so importing the plugin has no side effects.

Configuration (the runner passes no arguments):
    ALEMS_RAF_CTRL_PARAMS  path to controller_params.json (R5); absent: stub
    ALEMS_RAF_LAYA_URL     laya-serve base URL (R7); absent: laya_p fails closed

Fixed, token based and oracle arms need no decider: the profile builder
assigns their policy per goal (keeps task identity out of every decider, D3).
"""

import os
from typing import Any, Optional

from .base import ABANDON, OPTIONS, Outcome
from .beta_p import BetaP
from .failclosed import FailClosed
from .laya_p import LayaP
from .state import build_state
from .stubs import stub_params


def _load_beta() -> BetaP:
    path = os.environ.get("ALEMS_RAF_CTRL_PARAMS")
    return BetaP.from_file(path) if path else BetaP(stub_params())


class ControllerEntry:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._d: Optional[FailClosed] = None

    def _decider(self) -> FailClosed:
        if self._d is None:
            beta = _load_beta()
            url = os.environ.get("ALEMS_RAF_LAYA_URL")
            if self.kind == "laya" and url:
                prim = LayaP(beta, url)
            elif self.kind == "laya":
                prim = _Absent()
            else:
                prim = beta
            self._d = FailClosed(prim, beta)
        return self._d

    # ── seam: decide(attempt, executor) -> policy string or None ───
    def __call__(self, attempt: int, ex: Any) -> Optional[str]:
        prev = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        state = build_state(ex, attempt, prev)
        d = self._decider().decide(state, list(OPTIONS))
        det = dict(getattr(ex, "_raf_decision_detail", None) or {})
        det[attempt] = dict(d.detail(), work_source=state.work_source,
                            calls_done=state.calls_done,
                            failure_class=state.failure_class)
        ex._raf_decision_detail = det
        ex._raf_ctrl_options = prev + (d.option,)
        ex._raf_ctrl_class = state.failure_class
        return d.option

    # ── goal end hook (core C4) ─────────────────────────────────────
    def update_from_goal(self, ex: Any, passed: Optional[bool]) -> None:
        opts = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        if passed is None or not opts:
            return                      # needs review or no decision: no learning
        self._decider().update(Outcome(
            failure_class=str(getattr(ex, "_raf_ctrl_class", "unknown")),
            options=opts, passed=bool(passed), abandoned=opts[-1] == ABANDON,
            attempts=len(opts) + 1))


class _Absent:
    """laya_p configured but no URL: always fails closed, with a reason."""
    name = "laya_p"

    def decide(self, state, options):
        raise RuntimeError("laya-serve not configured")


ctrl_beta = ControllerEntry("beta")
ctrl_laya = ControllerEntry("laya")
