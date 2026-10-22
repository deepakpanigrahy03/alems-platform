"""
ear: the EAR decider for online study 2 (study_id e3b, coordinator Oct 22).

At a failure: p = sigmoid(intercept + sum coef[f] * x[f]) over the selector's
ordered features (one hot failure class, then calls_done), from
selector_lr_v2.json (path: ALEMS_RAF_SELECTOR). p > threshold (0.5): the
token_rule decider. Otherwise beta_p in mean mode: for each candidate option
the posterior mean alpha / (alpha + beta) from the controller_params_v2.json
priors only (counts empty: no history read, no draw), handed to beta_p.decide
as p_override, exactly as earctl Controller.decide(mode="mean").

Every decision record logs p, the chosen option, the sub decider, the
selector sha256 and path. No selector (missing or unreadable): no decision
(None keeps the scheduled policy) and no selector hash; the gates fail it.
Standard library only. Entry point alems.harness.deciders: ear.
"""

import hashlib
import json
import math
import os
from typing import Any, Dict, Optional, Tuple

from .adapter import _load_beta
from .base import INTERFACE_VERSION, OPTIONS
from .state import build_state
from .token_rule import token_rule

SELECTOR_ENV = "ALEMS_RAF_SELECTOR"


CLS_PREFIX = "cls="


def load_selector(path: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(selector, sha256) or (None, None) when the file is absent, unreadable or not R5's layout.

    R5 layout (selector_lr_v2.json): features ["cls=<c>", ..., "calls_done"], coef a list in
    feature order (a map is also accepted), intercept, threshold. classes is derived from the
    cls= features; any other feature name refuses the file (fail closed).
    """
    if not path:
        return None, None
    try:
        raw = open(path, "rb").read()
        sel = json.loads(raw.decode("utf-8"))
        feats = list(sel["features"])
        coef = sel["coef"]
        if isinstance(coef, list):
            if len(coef) != len(feats):
                return None, None
            coef = dict(zip(feats, coef))
        if set(coef) != set(feats) or float(sel["intercept"]) != float(sel["intercept"]):
            return None, None
        if any(f != "calls_done" and not f.startswith(CLS_PREFIX) for f in feats):
            return None, None
        sel = dict(sel, coef={f: float(v) for f, v in coef.items()},
                   classes=[f[len(CLS_PREFIX):] for f in feats if f.startswith(CLS_PREFIX)])
        return sel, hashlib.sha256(raw).hexdigest()
    except Exception:
        return None, None


def file_sha(path: Optional[str]) -> Optional[str]:
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest() if path else None
    except Exception:
        return None


def features(sel: Dict[str, Any], failure_class: str, calls_done: int) -> Dict[str, float]:
    """Feature values in the selector's own order: cls=<c> is 1 if the class equals c, else 0
    (an unseen class has all zeros); calls_done unscaled."""
    x = {}
    for f in sel["features"]:
        if f == "calls_done":
            x[f] = float(calls_done)
        else:
            x[f] = 1.0 if f[len(CLS_PREFIX):] == failure_class else 0.0
    return x


def probability(sel: Dict[str, Any], x: Dict[str, float]) -> float:
    z = float(sel["intercept"]) + sum(float(sel["coef"].get(f, 0.0)) * v for f, v in x.items())
    return 1.0 / (1.0 + math.exp(-z))


class Ear:
    name = "ear"

    def __init__(self) -> None:
        self._beta = None
        self._sel, self._sha = load_selector(os.environ.get(SELECTOR_ENV))

    def beta(self):
        if self._beta is None:
            self._beta = _load_beta()
        return self._beta

    def mean_decision(self, state):
        """earctl Controller.decide(mode="mean") with counts empty (priors only)."""
        b = self.beta()
        opts = list(OPTIONS)
        means = {}
        for o in b.candidates(state, opts):
            if o in state.previous_options:
                continue                     # tried options keep the repeat estimate
            a, bb = b.posterior(state, o, {})
            means[o] = a / (a + bb)
        return b.decide(state, opts, counts={}, seed=0, p_override=means, name="beta_mean"), means

    def __call__(self, attempt: int, ex: Any) -> Optional[str]:
        prev = tuple(getattr(ex, "_raf_ctrl_options", ()) or ())
        state = build_state(ex, attempt, prev)
        ctx = getattr(ex, "_raf_population", None) or {}
        pfile = os.environ.get("ALEMS_RAF_CTRL_PARAMS")
        base = {"decider": self.name, "arm": ctx.get("arm"), "study_id": ctx.get("study_id", "deploy"),
                "selector_sha256": self._sha, "selector_path": os.environ.get(SELECTOR_ENV),
                "params_file_sha256": file_sha(pfile),
                "selector_params_sha256": (self._sel or {}).get("params_sha256"),
                "class_in_selector": state.failure_class in ((self._sel or {}).get("classes") or []),
                "failure_class": state.failure_class, "calls_done": state.calls_done,
                "model_calls_done": state.model_calls_done, "work_source": state.work_source,
                "interface_version": INTERFACE_VERSION}
        det = dict(getattr(ex, "_raf_decision_detail", None) or {})
        if self._sel is None:
            det[attempt] = dict(base, option=None, decider_used=None, p=None,
                                fallback_reason="selector missing or unreadable")
            ex._raf_decision_detail = det
            return None
        x = features(self._sel, state.failure_class, state.calls_done)
        p = probability(self._sel, x)
        thr = float(self._sel.get("threshold", 0.5))
        if p > thr:
            option = token_rule(attempt, ex)             # records its own detail and option
            det = dict(getattr(ex, "_raf_decision_detail", None) or {})
            rec = dict(det.get(attempt) or {})
            rec.update(base, option=option, decider_used="token_rule", p=p, threshold=thr, x=x)
        else:
            d, means = self.mean_decision(state)
            option = d.option
            rec = dict(base, option=option, decider_used="beta_mean", p=p, threshold=thr, x=x,
                       params_version=d.params_version, means=means,
                       scores=[{"option": s.option, "p_success": s.p, "e_pred": s.e_pred,
                                "j_pred": s.j_pred, "p_source": s.p_source} for s in d.scores])
            ex._raf_ctrl_options = prev + (option,)
        det[attempt] = rec
        ex._raf_decision_detail = det
        return option


ear = Ear()
