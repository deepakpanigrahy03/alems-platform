"""RAF R5 controller model: pure functions shared with R6.

Spec: SPEC_RAF_R5_CONTROLLER_OFFLINE.md sections 2, 6.1 to 6.8, Addendum D.
Objective J = C / p (energy per achieved goal). Work done W at failure is the
decision variable for x* (D5), in calls, joules or context tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np

# ---------------------------------------------------------------- statistics

def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def p_smoothed(k: float, n: float, prior_k: float = 1.0, prior_n: float = 2.0) -> float:
    """Spec 2.2: prior of 1 success in 2 trials."""
    return (k + prior_k) / (n + prior_n)

# ---------------------------------------------------- per call energy law 6.1

@dataclass
class EnergyLaw:
    a: float
    b: float
    d: float
    ci: dict = field(default_factory=dict)   # name -> (lo, hi)
    n: int = 0

    def e(self, c):
        c = np.asarray(c, dtype=float)
        return self.a + self.b * c + self.d * c * c

    def cost(self, contexts) -> float:
        return float(np.sum(self.e(contexts)))


def _design(c):
    c = np.asarray(c, dtype=float)
    return np.column_stack([np.ones_like(c), c, c * c])


def fit_energy_law(c, e, clusters=None, n_boot: int = 1000, seed: int = 0,
                   quadratic: bool = True) -> EnergyLaw:
    """OLS of e = a + b c + d c^2; bootstrap CI resampling clusters (tasks)."""
    c = np.asarray(c, float); e = np.asarray(e, float)
    X = _design(c) if quadratic else _design(c)[:, :2]
    beta = np.linalg.lstsq(X, e, rcond=None)[0]
    if not quadratic:
        beta = np.append(beta, 0.0)
    law = EnergyLaw(*map(float, beta), n=len(c))
    if n_boot and len(c) > 3:
        rng = np.random.default_rng(seed)
        groups = np.arange(len(c)) if clusters is None else np.asarray(clusters)
        ug = np.unique(groups)
        idx_by = {g: np.flatnonzero(groups == g) for g in ug}
        draws = []
        for _ in range(n_boot):
            pick = rng.choice(ug, size=len(ug), replace=True)
            ii = np.concatenate([idx_by[g] for g in pick])
            b = np.linalg.lstsq(X[ii], e[ii], rcond=None)[0]
            draws.append(np.append(b, 0.0) if not quadratic else b)
        D = np.array(draws)
        for j, name in enumerate("abd"):
            law.ci[name] = (float(np.percentile(D[:, j], 2.5)),
                            float(np.percentile(D[:, j], 97.5)))
    return law


class RLS:
    """Recursive least squares for (a, b, d), outer loop 6.6 item 2."""

    def __init__(self, law: EnergyLaw | None = None, delta: float = 1e6,
                 scale: float = 1e4):
        self.s = scale                      # token rescale for conditioning
        self.th = np.zeros(3) if law is None else np.array(
            [law.a, law.b * scale, law.d * scale * scale])
        self.P = np.eye(3) * delta

    def update(self, c: float, e: float) -> None:
        x = np.array([1.0, c / self.s, (c / self.s) ** 2])
        Px = self.P @ x
        k = Px / (1.0 + x @ Px)
        self.th = self.th + k * (e - x @ self.th)
        self.P = self.P - np.outer(k, Px)

    def law(self) -> EnergyLaw:
        a, b, d = self.th
        return EnergyLaw(float(a), float(b / self.s), float(d / self.s ** 2))

# ------------------------------------------------- depth cost model 6.1, 6.2

def cumulative_work(law: EnergyLaw, contexts) -> np.ndarray:
    """W(k): joules of the first k calls, k = 0..n."""
    return np.concatenate([[0.0], np.cumsum(law.e(contexts))])


def J_depth(law: EnergyLaw, contexts, k_fail: int, x: int, p_of_x,
            overhead=lambda x, attempt: 0.0, attempt: int = 2) -> float:
    """Expected energy per success when keeping prefix x of k_fail done calls.

    contexts: context tokens of each call of the clean trajectory.
    Redo = calls x..k_fail-1 again, continue = calls k_fail..n-1. Already spent
    W(k_fail) is sunk but counted, so J is energy per achieved goal.
    """
    Wc = cumulative_work(law, contexts)
    sunk = Wc[k_fail]
    redo = Wc[k_fail] - Wc[x]
    cont = Wc[-1] - Wc[k_fail]
    return (sunk + redo + cont + overhead(x, attempt)) / max(p_of_x(x), 1e-9)


def best_depth(law, contexts, k_fail, p_of_x, overhead=lambda x, a: 0.0):
    Js = [J_depth(law, contexts, k_fail, x, p_of_x, overhead) for x in range(k_fail + 1)]
    return int(np.argmin(Js)), Js


def fit_overhead(ctx, attempt, energy_excess):
    """O(c, attempt) = o0 + o1 c + o2 c (attempt - 2), 6.2. Least squares."""
    ctx = np.asarray(ctx, float); at = np.asarray(attempt, float)
    X = np.column_stack([np.ones_like(ctx), ctx, ctx * (at - 2)])
    th = np.linalg.lstsq(X, np.asarray(energy_excess, float), rcond=None)[0]
    return lambda c, a: float(th[0] + th[1] * c + th[2] * c * (a - 2)), th

# -------------------------------------------- closed form threshold x*, 6.8

def xstar_joules(C_full: float, p_restart: float, p_resume: float,
                 O_restart: float = 0.0, O_resume: float = 0.0) -> float:
    """Work done W* below which restart has the lower J.

    J_resume = (W + (C - W) + O_s) / p_s = (C + O_s) / p_s   (prefix kept)
    J_restart = (W + C + O_r) / p_r
    restart wins  <=>  W < (C + O_s) p_r / p_s - C - O_r.
    Negative W* means resume always wins.
    """
    return (C_full + O_resume) * p_restart / max(p_resume, 1e-9) - C_full - O_restart


def joules_to_calls(law: EnergyLaw, contexts, W: float) -> float:
    """Invert cumulative work along a trajectory; fractional call count."""
    Wc = cumulative_work(law, contexts)
    if W <= 0:
        return 0.0
    if W >= Wc[-1]:
        return float(len(contexts))
    k = int(np.searchsorted(Wc, W)) - 1
    return k + (W - Wc[k]) / (Wc[k + 1] - Wc[k])


def calls_to_tokens(contexts, n_calls: float) -> float:
    if n_calls <= 0:
        return 0.0
    i = min(int(math.ceil(n_calls)) - 1, len(contexts) - 1)
    return float(contexts[i])

# ------------------------------------------- Beta posteriors (outer loop)

class BetaBank:
    """Beta(alpha, beta) per key = (class, option[, 'repeat'])."""

    def __init__(self, prior_alpha: float = 1.0, prior_beta: float = 1.0):
        self.a0, self.b0 = prior_alpha, prior_beta
        self.ab: dict = {}

    def get(self, key):
        return self.ab.get(key, (self.a0, self.b0))

    def seed(self, key, successes: float, failures: float, weight: float = 1.0):
        a, b = self.get(key)
        self.ab[key] = (a + weight * successes, b + weight * failures)

    def update(self, key, success: bool):
        a, b = self.get(key)
        self.ab[key] = (a + (1 if success else 0), b + (0 if success else 1))

    def mean(self, key) -> float:
        a, b = self.get(key)
        return a / (a + b)

    def sample(self, key, rng) -> float:
        a, b = self.get(key)
        return float(rng.beta(a, b))

    def to_json(self):
        return {"|".join(map(str, k)): {"alpha": v[0], "beta": v[1],
                                        "mean": v[0] / (v[0] + v[1])}
                for k, v in sorted(self.ab.items())}

# ------------------------------------------ decision and escalation (6.3)

def decide(cls: str, options: dict, bank: BetaBank, budget: float | None = None,
           tried: dict | None = None, thompson_rng=None):
    """Choose the option with least J = E2(o) / p(o), or abandon.

    options: option -> predicted additional energy E2 (joules).
    tried: option -> number of failed uses in this goal (inner loop).
    The first failure of an option moves later choices to the 'repeat'
    posterior for (cls, option), which E2 persistent cells estimate.
    Order of rungs is not fixed: it comes from cost and success data.
    """
    tried = tried or {}
    scores = {}
    for o, E2 in options.items():
        key = (cls, o, "repeat") if tried.get(o, 0) > 0 else (cls, o)
        p = bank.sample(key, thompson_rng) if thompson_rng is not None else bank.mean(key)
        scores[o] = E2 / max(p, 1e-9)
    best = min(scores, key=scores.get)
    if budget is not None and scores[best] > budget:
        return "abandon", scores
    return best, scores


def history_free_rule(W_calls: float, theta: float, contaminating: bool) -> str:
    """Spec 2.3, written in work done: restart below theta, else resume."""
    if W_calls < theta:
        return "FR_warm"
    return "RB_warm" if contaminating else "SR_warm"

# --------------------------------------------- E2 linear model (spec 2.2)

def fit_e2(W, E2):
    """E2 = a + b W per (class, option); returns (a, b)."""
    W = np.asarray(W, float); E2 = np.asarray(E2, float)
    if len(W) < 2 or np.ptp(W) == 0:
        return float(np.mean(E2)), 0.0
    b, a = np.polyfit(W, E2, 1)
    return float(a), float(b)
