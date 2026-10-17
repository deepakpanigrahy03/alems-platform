"""
xstar_rule: the history free threshold baseline in E3 (coordinator, Oct 17).

Restart (FR_warm) if calls done before the failure is below x* of the
observable class, else step resume (SR_warm). Warm always (R5 2.3); roll
back is not available to it. x* comes from R5's predict.restart_below_threshold
(_default fallback for classes without a fit). Same inputs as the
controller, no point label, no learning, no abandon.

Entry point alems.harness.deciders: xstar_rule.
"""

from typing import Any, Optional

from . import predict as R5
from .adapter import _load_beta
from .base import INTERFACE_VERSION
from .state import build_state

RESTART, RESUME = "FR_warm", "SR_warm"


class XStarRule:
    name = "xstar_rule"

    def __init__(self) -> None:
        self._beta = None

    def _params(self):
        if self._beta is None:
            self._beta = _load_beta()
        return self._beta.params

    def choose(self, failure_class: str, calls_done: int) -> dict:
        below = R5.restart_below_threshold(self._params(), failure_class, calls_done)
        x = R5._cls(self._params(), failure_class).get("_x_star_calls")
        if below is None:
            return {"option": RESUME, "x_star_calls": None, "reason": "no x* for class"}
        return {"option": RESTART if below else RESUME, "x_star_calls": x, "reason": None}

    def __call__(self, attempt: int, ex: Any) -> Optional[str]:
        prev = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        state = build_state(ex, attempt, prev)
        res = self.choose(state.failure_class, state.calls_done)
        ctx = getattr(ex, "_raf_population", None) or {}
        self._params()
        rec = {"params_version": self._beta.version, "data_tag": self._beta.data_tag,
               "decider": self.name, "arm": ctx.get("arm"),
               "study_id": ctx.get("study_id", "deploy"),
               "option": res["option"], "decider_used": self.name,
               "x_star_calls": res["x_star_calls"], "fallback_reason": res["reason"],
               "failure_class": state.failure_class, "calls_done": state.calls_done,
               "model_calls_done": state.model_calls_done, "work_source": state.work_source,
               "interface_version": INTERFACE_VERSION}
        det = dict(getattr(ex, "_raf_decision_detail", None) or {})
        det[attempt] = rec
        ex._raf_decision_detail = det
        ex._raf_ctrl_options = prev + (res["option"],)
        return res["option"]


xstar_rule = XStarRule()
