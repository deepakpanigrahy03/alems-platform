"""RAF controller prediction at decision time (owner: R5). Standard library only.

Inputs known at failure time: failure class as seen in the failure record,
calls done before the failure (model plus tool, same unit as x*), prompt and
cached tokens of the last call, options already tried in this goal.
Never used: joules of attempt 1, task id, early or late labels.

Params layout (controller_params.schema.json, extended per R5/R6 Oct 16):
  classes.<cls>.options.<option> = {alpha, beta, alpha_raw, beta_raw,
                                    e2_a, e2_b, e2_g, e2_h, p_repeat_after_fail}
  classes.<cls>._x_star_calls, ._x_star_calls_ci, ._budget_j
  classes._default  (pooled; used for any class without a fit)
"""
from __future__ import annotations

import json
import random

DEFAULT = "_default"


def load_params(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _cls(params: dict, failure_class: str) -> dict:
    classes = params.get("classes", {})
    return classes.get(failure_class) or classes.get(DEFAULT) or {}


def _opt(params: dict, failure_class: str, option: str) -> dict | None:
    c = _cls(params, failure_class)
    o = c.get("options", {}).get(option)
    if o is None and failure_class != DEFAULT:
        o = params.get("classes", {}).get(DEFAULT, {}).get("options", {}).get(option)
    return o


def predict_energy(params, failure_class, option, calls_done, tokens_context,
                   tokens_cached, attempt=2):
    """Expected additional joules to finish the goal with `option`; None if unfitted."""
    o = _opt(params, failure_class, option)
    if o is None or o.get("e2_a") is None:
        return None
    e = (o["e2_a"] + o.get("e2_b", 0.0) * calls_done
         + o.get("e2_g", 0.0) * (tokens_context or 0)
         + o.get("e2_h", 0.0) * (tokens_cached or 0))
    ov = params.get("overhead")
    if ov and attempt > 2:
        e += ov.get("o0", 0.0) + ov.get("o1", 0.0) * (tokens_context or 0) \
            + ov.get("o2", 0.0) * (tokens_context or 0) * (attempt - 2)
    return max(e, 0.0)


def p_success(params, failure_class, option, failed_before=0, rng=None):
    """Posterior mean (or Thompson draw with rng) of success for `option`.
    After a failed use of the same option in this goal, the repeat estimate
    replaces it (inner loop, spec 6.3)."""
    o = _opt(params, failure_class, option)
    if o is None:
        return None
    if failed_before > 0 and o.get("p_repeat_after_fail") is not None:
        return float(o["p_repeat_after_fail"])
    a, b = float(o.get("alpha", 1.0)), float(o.get("beta", 1.0))
    return rng.betavariate(a, b) if rng is not None else a / (a + b)


def expected_J(params, failure_class, option, calls_done, tokens_context,
               tokens_cached, attempt=2, failed_before=0, rng=None):
    e = predict_energy(params, failure_class, option, calls_done,
                       tokens_context, tokens_cached, attempt)
    p = p_success(params, failure_class, option, failed_before, rng)
    if e is None or p is None:
        return None
    return e / max(p, 1e-6)


def decide(params, failure_class, options, calls_done, tokens_context,
           tokens_cached, attempt=2, tried=None, rng: random.Random | None = None):
    """Return (choice, scores). choice is an option or 'abandon'.
    Abandon only when the best J exceeds the class budget."""
    tried = tried or {}
    scores = {}
    for opt in options:
        j = expected_J(params, failure_class, opt, calls_done, tokens_context,
                       tokens_cached, attempt, tried.get(opt, 0), rng)
        if j is not None:
            scores[opt] = j
    if not scores:
        return "abandon", scores
    best = min(scores, key=scores.get)
    budget = _cls(params, failure_class).get("_budget_j")
    if budget is not None and scores[best] > budget:
        return "abandon", scores
    return best, scores


def restart_below_threshold(params, failure_class, calls_done) -> bool | None:
    """Practitioner rule (spec 6.8): True if work done is below x*."""
    x = _cls(params, failure_class).get("_x_star_calls")
    return None if x is None else calls_done < x
