"""
token_rule: the E3 token based baseline (coordinator, Oct 16 17:23).

Same inputs as the controller, no point label: per observable class and
calls done at failure (x* unit), choose the option with the lowest predicted
uncached prompt tokens for attempt 2. The prediction comes from R5's
predict.py (predict_uncached_tokens, fitted on E1, E1b, E2 in the same
params file). No learning, no energy, no abandon.

Entry point alems.harness.deciders: token_rule. Decisions carry a
population (decider token_rule) so they are written to the store like the
controller's and are never counted as controller evidence.
"""

from typing import Any, Dict, Optional

from . import predict as R5
from .adapter import _load_beta
from .base import CACHES, DEPTHS, INTERFACE_VERSION
from .state import build_state

OPTIONS = tuple("%s_%s" % (d, c) for d in DEPTHS for c in CACHES)


class TokenRule:
    name = "token_rule"

    def __init__(self) -> None:
        self._beta = None

    def _params(self) -> Dict[str, Any]:
        if self._beta is None:
            self._beta = _load_beta()
        return self._beta.params

    @staticmethod
    def _from_params(params: Dict[str, Any], failure_class: str, option: str,
                     calls_done: int) -> Optional[float]:
        """tok_a + tok_b · calls_done from R5's params (_default fallback via R5._opt)."""
        o = R5._opt(params, failure_class, option) or {}
        if o.get("tok_a") is None:
            return None
        return float(o["tok_a"]) + float(o.get("tok_b") or 0.0) * calls_done

    def choose(self, failure_class: str, calls_done: int) -> Dict[str, Any]:
        """Option with the lowest predicted uncached tokens; None when nothing is fitted."""
        fn = getattr(R5, "predict_uncached_tokens", None)
        source = "predict_uncached_tokens" if callable(fn) else "params_tok_a_tok_b"
        if not callable(fn):
            fn = self._from_params
        pred = {o: fn(self._params(), failure_class, o, calls_done) for o in OPTIONS}
        pred = {o: v for o, v in pred.items() if v is not None}
        if not pred:
            return {"option": None, "tokens": {}, "reason": "no token fit for class",
                    "source": source}
        best = min(sorted(pred), key=lambda o: pred[o])
        return {"option": best, "tokens": pred, "reason": None, "source": source}

    def __call__(self, attempt: int, ex: Any) -> Optional[str]:
        prev = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        state = build_state(ex, attempt, prev)
        res = self.choose(state.failure_class, state.calls_done)
        ctx = getattr(ex, "_raf_population", None) or {}
        self._params()
        rec = {"params_version": self._beta.version, "data_tag": self._beta.data_tag,
               "decider": self.name, "arm": ctx.get("arm"),
               "study_id": ctx.get("study_id", "deploy"),
               "option": res["option"] or "keep", "decider_used": self.name,
               "predicted_uncached_tokens": res["tokens"], "fallback_reason": res["reason"],
               "token_source": res.get("source"),
               "failure_class": state.failure_class, "calls_done": state.calls_done,
               "model_calls_done": state.model_calls_done, "work_source": state.work_source,
               "interface_version": INTERFACE_VERSION}
        det = dict(getattr(ex, "_raf_decision_detail", None) or {})
        det[attempt] = rec
        ex._raf_decision_detail = det
        if res["option"]:
            ex._raf_ctrl_options = prev + (res["option"],)
        return res["option"]                       # None keeps the schedule's policy


token_rule = TokenRule()
