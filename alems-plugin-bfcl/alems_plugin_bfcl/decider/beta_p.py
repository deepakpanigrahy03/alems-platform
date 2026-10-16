"""
beta_p: Beta posterior per (observable class, option), Thompson sampled.

Numbers come from R5's predict.py (one source of truth): predicted energy,
the option prior (alpha, beta, p_repeat_after_fail with the _default
fallback), x* and the class budget. beta_p adds what only the online
controller has: per population counts from the store, the inner loop,
escalation (R5 6.3), the x* gate (R5 6.8), the abandon budget (R5 2.2)
and the Laya override.

    alpha = prior alpha + successes      beta = prior beta + failures + failures in this goal
    p ~ Beta(alpha, beta), one seeded sample per option in fixed order
    J = predict_energy / p

Pure Python, no store, no network: counts arrive as an argument.
"""

import json
import random
from typing import Any, Dict, List, Optional, Tuple

from . import predict as R5
from .base import (ABANDON, Decision, FailureState, OptionScore, ladder_rung)

PRIOR = (1.0, 1.0)                    # one success in two trials (R5 2.2)


class BetaP:
    name = "beta_p"

    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params
        prov = params.get("provenance") or {}
        self.version = str(params.get("params_version") or prov.get("sha256") or "stub")
        self.data_tag = str(params.get("data_tag") or prov.get("data_tag") or "none")

    @classmethod
    def from_file(cls, path: str) -> "BetaP":
        with open(path) as fh:
            return cls(json.load(fh))

    # ── model pieces (numbers from R5) ──────────────────────────────
    def _opt(self, cls: str, opt: str) -> Dict[str, Any]:
        prior = getattr(R5, "prior", None)            # public once R5 adds it
        o = prior(self.params, cls, opt) if callable(prior) else R5._opt(self.params, cls, opt)
        return o or {}

    def _budget(self, cls: str) -> Optional[float]:
        b = getattr(R5, "budget_j", None)
        if callable(b):
            return b(self.params, cls)
        return R5._cls(self.params, cls).get("_budget_j")

    def x_star(self, cls: str) -> Optional[float]:
        return R5._cls(self.params, cls).get("_x_star_calls")

    def posterior(self, state: FailureState, opt: str,
                  counts: Dict[str, Any]) -> Tuple[float, float]:
        op = self._opt(state.failure_class, opt)
        a0 = float(op.get("alpha", PRIOR[0]))
        b0 = float(op.get("beta", PRIOR[1]))
        s, f = (counts or {}).get("%s|%s" % (state.failure_class, opt), (0, 0))
        # Inner loop: each failure of this option in this goal is one more failure.
        return a0 + s, b0 + f + state.previous_options.count(opt)

    def energy(self, state: FailureState, opt: str) -> Optional[float]:
        return R5.predict_energy(self.params, state.failure_class, opt, state.calls_done,
                                 state.tokens_context, state.tokens_cached,
                                 attempt=state.attempt)

    # ── Decider ─────────────────────────────────────────────────────
    def candidates(self, state: FailureState, options: List[str]) -> List[str]:
        opts = sorted(o for o in options if o != ABANDON)   # fixed sampling order
        if R5.restart_below_threshold(self.params, state.failure_class, state.calls_done):
            # Below x*, restart beats resume (R5 6.8): restart options only.
            opts = [o for o in opts if o.startswith("FR_")] or opts
        return opts

    def _p(self, state: FailureState, o: str, counts: Dict[str, Any],
           rng: random.Random, p_override: Optional[Dict[str, float]]) -> float:
        a, b = self.posterior(state, o, counts)
        sample = rng.betavariate(a, b)   # drawn for every option: stream stays aligned
        if p_override is not None and o in p_override:
            return float(p_override[o])
        rep = self._opt(state.failure_class, o).get("p_repeat_after_fail")
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
        budget = self._budget(state.failure_class)
        # Abandon only through the budget (R5): never because a fit is missing.
        if (ABANDON in options and budget is not None and best.j_pred is not None
                and best.j_pred > float(budget)):
            best_opt = ABANDON
        else:
            best_opt = best.option
        return Decision(option=best_opt, decider=name or self.name,
                        params_version=self.version, scores=tuple(scores),
                        x_star_calls=self.x_star(state.failure_class))

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
