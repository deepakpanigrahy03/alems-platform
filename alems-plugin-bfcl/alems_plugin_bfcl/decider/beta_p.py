"""
beta_p: class level Beta posterior with the analytical cost (R5 6.1 to 6.6).

J(o) = E_pred(o) / p(o). E_pred uses the per class and option line of R5
section 2.2 in work done units (calls): E = e2_a + e2_b * calls_done.
Inner loop (R5 6.3): an option that already failed in this goal has its p
lowered by one observed failure, or by p_repeat_after_fail when R5 supplies
it; the choice never moves down the ladder. Outer loop (R5 6.6): update()
adds the checker result of the finished goal to the posterior.

Pure Python, no store, no network, so decide() is fast and deterministic.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from .base import (ABANDON, Decision, FailureState, OptionScore, Outcome,
                   ladder_rung)

PRIOR = (1.0, 1.0)                    # one success in two trials (R5 2.2)


class BetaP:
    name = "beta_p"

    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params
        self.version = str(params.get("params_version", "stub"))
        # Posterior copy: params stay immutable, learning lives here.
        self.post: Dict[Tuple[str, str], List[float]] = {}
        for cls, opts in (params.get("classes") or {}).items():
            for opt, v in opts.items():
                self.post[(cls, opt)] = [float(v["alpha"]), float(v["beta"])]

    @classmethod
    def from_file(cls, path: str) -> "BetaP":
        with open(path) as fh:
            return cls(json.load(fh))

    # ── model pieces ────────────────────────────────────────────────
    def _ab(self, cls: str, opt: str) -> List[float]:
        return self.post.setdefault((cls, opt), list(PRIOR))

    def _opt_params(self, cls: str, opt: str) -> Dict[str, Any]:
        return ((self.params.get("classes") or {}).get(cls) or {}).get(opt) or {}

    def p(self, state: FailureState, opt: str) -> float:
        a, b = self._ab(state.failure_class, opt)
        fails = state.previous_options.count(opt)
        rep = self._opt_params(state.failure_class, opt).get("p_repeat_after_fail")
        if fails and rep is not None:
            return float(rep)
        # Each failure of this option in this goal counts as one more failure.
        return a / (a + b + fails)

    def energy(self, state: FailureState, opt: str) -> Optional[float]:
        op = self._opt_params(state.failure_class, opt)
        ea, eb = op.get("e2_a"), op.get("e2_b")
        if ea is None or eb is None:
            return None
        return float(ea["value"]) + float(eb["value"]) * state.calls_done

    # ── Decider ─────────────────────────────────────────────────────
    def candidates(self, state: FailureState, options: List[str]) -> List[str]:
        opts = [o for o in options if o != ABANDON]
        x = (self.params.get("x_star_calls") or {}).get("value")
        if x is not None and state.calls_done < float(x):
            # Below x*, restart beats resume (R5 6.8): restart options only.
            opts = [o for o in opts if o.startswith("FR_")] or opts
        return opts

    def decide(self, state: FailureState, options: List[str],
               p_override: Optional[Dict[str, float]] = None,
               name: Optional[str] = None) -> Decision:
        scores = []
        for o in self.candidates(state, options):
            p = (p_override or {}).get(o, self.p(state, o))
            e = self.energy(state, o)
            j = None if e is None or p <= 0 else e / p
            scores.append(OptionScore(option=o, p=p, j_pred=j, e_pred=e))
        scores = self._escalate(state, scores)
        best = self._pick(scores)
        budget = (self.params.get("budget") or {}).get("value_j")
        if (ABANDON in options and budget is not None and best.j_pred is not None
                and best.j_pred > float(budget)):
            best_opt = ABANDON
        else:
            best_opt = best.option
        x = (self.params.get("x_star_calls") or {}).get("value")
        return Decision(option=best_opt, decider=name or self.name,
                        params_version=self.version, scores=tuple(scores),
                        x_star_calls=x)

    def _escalate(self, state: FailureState,
                  scores: List[OptionScore]) -> List[OptionScore]:
        """R5 6.3: never repeat a failed option whose updated J exceeds the next rung."""
        tried = set(state.previous_options)
        keep = []
        for s in scores:
            nxt = [t for t in scores
                   if ladder_rung(t.option) == ladder_rung(s.option) + 1]
            # An untried option, or one with no scored next rung, always stays.
            if s.option in tried and nxt and self._worse(s, self._pick(nxt)):
                continue
            keep.append(s)
        return keep or scores

    @staticmethod
    def _worse(a: OptionScore, b: OptionScore) -> bool:
        # Compare on J when both have energy; otherwise on p (stub phase).
        if a.j_pred is not None and b.j_pred is not None:
            return a.j_pred > b.j_pred
        return (a.p or 0.0) < (b.p or 0.0)

    @staticmethod
    def _pick(scores: List[OptionScore]) -> OptionScore:
        # Lowest J; without energy parameters, highest p (stub phase).
        with_j = [s for s in scores if s.j_pred is not None]
        if with_j:
            return min(with_j, key=lambda s: (s.j_pred, s.option))
        return max(scores, key=lambda s: (s.p or 0.0, s.option))

    def update(self, outcome: Outcome) -> None:
        if not outcome.options:
            return
        for i, opt in enumerate(outcome.options):
            if opt == ABANDON:
                continue
            ab = self._ab(outcome.failure_class, opt)
            last = i == len(outcome.options) - 1
            if last and outcome.passed:
                ab[0] += 1.0
            else:
                ab[1] += 1.0
