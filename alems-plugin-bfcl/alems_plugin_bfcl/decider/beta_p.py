"""
beta_p: Beta posterior per (failure class, option), Thompson sampled (design v2).

posterior = R5 prior + counts read from the store (raf_history) + failures of
the option earlier in this goal. One seeded sample per option in fixed order;
J(o) = E_pred(o) / p(o). Escalation (R5 6.3), the x* gate (R5 6.8) and the
abandon budget (R5 2.2) operate on the sampled p.

Pure Python, no store, no network: counts arrive as an argument.
"""

import json
import random
from typing import Any, Dict, List, Optional, Tuple

from .base import (ABANDON, Decision, FailureState, OptionScore, ladder_rung)

PRIOR = (1.0, 1.0)                    # one success in two trials (R5 2.2)


class BetaP:
    name = "beta_p"

    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params
        self.version = str(params.get("params_version", "stub"))
        self.data_tag = str(params.get("data_tag", "none"))

    @classmethod
    def from_file(cls, path: str) -> "BetaP":
        with open(path) as fh:
            return cls(json.load(fh))

    # ── model pieces ────────────────────────────────────────────────
    def _opt_params(self, cls: str, opt: str) -> Dict[str, Any]:
        return ((self.params.get("classes") or {}).get(cls) or {}).get(opt) or {}

    def posterior(self, state: FailureState, opt: str,
                  counts: Dict[str, Any]) -> Tuple[float, float]:
        op = self._opt_params(state.failure_class, opt)
        a0 = float(op.get("alpha", PRIOR[0]))
        b0 = float(op.get("beta", PRIOR[1]))
        s, f = (counts or {}).get("%s|%s" % (state.failure_class, opt), (0, 0))
        # Inner loop: each failure of this option in this goal is one more failure.
        return a0 + s, b0 + f + state.previous_options.count(opt)

    def energy(self, state: FailureState, opt: str) -> Optional[float]:
        op = self._opt_params(state.failure_class, opt)
        ea, eb = op.get("e2_a"), op.get("e2_b")
        if ea is None or eb is None:
            return None
        return float(ea["value"]) + float(eb["value"]) * state.calls_done

    # ── Decider ─────────────────────────────────────────────────────
    def candidates(self, state: FailureState, options: List[str]) -> List[str]:
        opts = sorted(o for o in options if o != ABANDON)   # fixed sampling order
        x = (self.params.get("x_star_calls") or {}).get("value")
        if x is not None and state.calls_done < float(x):
            # Below x*, restart beats resume (R5 6.8): restart options only.
            opts = [o for o in opts if o.startswith("FR_")] or opts
        return opts

    def _p(self, state: FailureState, o: str, counts: Dict[str, Any],
           rng: random.Random, p_override: Optional[Dict[str, float]]) -> float:
        a, b = self.posterior(state, o, counts)
        sample = rng.betavariate(a, b)   # drawn for every option: stream stays aligned
        if p_override is not None and o in p_override:
            return float(p_override[o])
        rep = self._opt_params(state.failure_class, o).get("p_repeat_after_fail")
        if rep is not None and o in state.previous_options:
            return float(rep)            # R5 6.6 item 3
        return sample

    def decide(self, state: FailureState, options: List[str],
               counts: Optional[Dict[str, Any]] = None, seed: int = 0,
               p_override: Optional[Dict[str, float]] = None,
               name: Optional[str] = None) -> Decision:
        rng = random.Random(seed)
        scores = []
        for o in self.candidates(state, options):
            p = self._p(state, o, counts or {}, rng, p_override)
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
        """R5 6.3: never repeat a failed option whose J exceeds the next rung."""
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
