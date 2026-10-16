"""
Entry point objects for alems.harness.deciders (seam of R2: decide(attempt, executor)).

ctrl_beta and ctrl_laya are callable objects loaded once per arm. They hold
no learned state: learning is read from the store through the history
function core places on the executor (design v2 section 7).

Configuration:
    ALEMS_RAF_CTRL_PARAMS  path to controller_params.json (R5); absent: stub
    ALEMS_RAF_LAYA_URL     laya-serve base URL (R7); absent: laya_p fails closed
Population context (arm, study_id, study_seed) and the history function are
set on the executor by the scheduled runner before each goal.
"""

import hashlib
import os
from typing import Any, Dict, Optional

from .base import OPTIONS
from .beta_p import BetaP
from .failclosed import FailClosed
from .laya_p import LayaP
from .state import build_state
from .stubs import stub_params

_NAMES = {"beta": "beta_p", "laya": "laya_p"}


def _load_beta() -> BetaP:
    path = os.environ.get("ALEMS_RAF_CTRL_PARAMS")
    return BetaP.from_file(path) if path else BetaP(stub_params())


def decision_seed(study_seed: Any, goal_key: Any, attempt: int) -> int:
    """SHA-256 of (study seed, goal key, attempt) to 64 bits; seeds only (D3)."""
    raw = "%s|%s|%d" % (study_seed, goal_key, attempt)
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)


class ControllerEntry:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._d: Optional[FailClosed] = None
        self._beta: Optional[BetaP] = None

    def _decider(self) -> FailClosed:
        if self._d is None:
            self._beta = _load_beta()
            url = os.environ.get("ALEMS_RAF_LAYA_URL")
            if self.kind == "laya" and url:
                prim = LayaP(self._beta, url)
            elif self.kind == "laya":
                prim = _Absent()
            else:
                prim = self._beta
            self._d = FailClosed(prim, self._beta)
        return self._d

    def population(self, ex: Any) -> Dict[str, Any]:
        ctx = getattr(ex, "_raf_population", None) or {}
        self._decider()
        return {"params_version": self._beta.version, "data_tag": self._beta.data_tag,
                "decider": _NAMES[self.kind], "arm": ctx.get("arm"),
                "study_id": ctx.get("study_id", "deploy")}

    def _history(self, ex: Any, pop: Dict[str, Any]) -> Dict[str, Any]:
        # Read once per goal at its first decision; later attempts reuse it.
        cached = getattr(ex, "_raf_hist_cache", None)
        if cached is not None:
            return cached
        fn = getattr(ex, "_raf_history", None)
        hist = fn(pop) if callable(fn) else {"counts": {}, "cut_id": None,
                                              "goals": 0, "query_ns": 0}
        ex._raf_hist_cache = hist
        return hist

    # ── seam: decide(attempt, executor) -> policy string ─────────────
    def __call__(self, attempt: int, ex: Any) -> Optional[str]:
        prev = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        state = build_state(ex, attempt, prev)
        pop = self.population(ex)
        hist = self._history(ex, pop)
        ctx = getattr(ex, "_raf_population", None) or {}
        # Reproducible across reruns: the fixed goal identity plus arm; the
        # goal key (random suffix) is only a fallback outside the runner.
        key = ctx.get("seed_key")
        if key:
            key = "%s|%s" % (key, ctx.get("arm"))
        else:
            key = (getattr(ex, "_raf_context", None) or {}).get("goal_key")
        seed = decision_seed(ctx.get("study_seed"), key, attempt)
        d = self._decider().decide(state, list(OPTIONS), counts=hist["counts"], seed=seed)
        # Population fields win over the Decision's own decider name; the
        # decider actually used (fallback or not) stays in decider_used.
        rec = dict(d.detail(), **pop)
        rec.update(option=d.option, decider_used=d.decider, seed=seed, counts=hist["counts"],
                   cut_id=hist["cut_id"], history_goals=hist["goals"],
                   history_query_ns=hist["query_ns"], work_source=state.work_source,
                   calls_done=state.calls_done, failure_class=state.failure_class)
        det = dict(getattr(ex, "_raf_decision_detail", None) or {})
        det[attempt] = rec
        ex._raf_decision_detail = det
        ex._raf_ctrl_options = prev + (d.option,)
        return d.option


class _Absent:
    """laya_p configured but no URL: always fails closed, with a reason."""
    name = "laya_p"

    def decide(self, state, options, counts=None, seed=0):
        raise RuntimeError("laya-serve not configured")


ctrl_beta = ControllerEntry("beta")
ctrl_laya = ControllerEntry("laya")
