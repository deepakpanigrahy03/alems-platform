import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import controller_model as cm  # noqa: E402


def test_energy_law_recovers_parameters():
    rng = np.random.default_rng(1)
    c = rng.uniform(1e3, 3e4, 600)
    e = 40 + 2e-3 * c + 5e-8 * c * c + rng.normal(0, 1, c.size)
    law = cm.fit_energy_law(c, e, clusters=rng.integers(0, 20, c.size), n_boot=200)
    assert law.a == pytest.approx(40, abs=1)
    assert law.b == pytest.approx(2e-3, rel=0.05)
    assert law.d == pytest.approx(5e-8, rel=0.05)
    assert law.ci["d"][0] < 5e-8 < law.ci["d"][1]


def test_rls_converges_to_ols():
    rng = np.random.default_rng(2)
    c = rng.uniform(1e3, 3e4, 2000)
    e = 40 + 2e-3 * c + 5e-8 * c * c
    r = cm.RLS()
    for ci, ei in zip(c, e):
        r.update(ci, ei)
    L = r.law()
    assert L.a == pytest.approx(40, rel=1e-2) and L.d == pytest.approx(5e-8, rel=1e-2)


def test_decision_picks_cheapest_J():
    bank = cm.BetaBank()
    bank.seed(("t", "A"), 9, 1)
    bank.seed(("t", "B"), 4, 6)
    o, s = cm.decide("t", {"A": 1000.0, "B": 600.0}, bank)
    assert o == "A"                      # 1000/0.83 < 600/0.42


def test_abandon_above_budget():
    bank = cm.BetaBank()
    o, _ = cm.decide("t", {"A": 1000.0}, bank, budget=500.0)
    assert o == "abandon"


def test_beta_update_counts():
    b = cm.BetaBank()
    for y in [1, 1, 0]:
        b.update(("c", "SR"), y)
    assert b.get(("c", "SR")) == (3.0, 2.0)


def test_escalation_uses_repeat_posterior_and_skips_dominated():
    b = cm.BetaBank()
    b.seed(("c", "SR"), 8, 2)
    b.seed(("c", "FR"), 9, 1)
    b.seed(("c", "SR", "repeat"), 1, 9)  # same class again: wrong key
    first, _ = cm.decide("c", {"SR": 500.0, "FR": 900.0}, b)
    assert first == "SR"
    second, _ = cm.decide("c", {"SR": 500.0, "FR": 900.0}, b, tried={"SR": 1})
    assert second == "FR"


def test_xstar_closed_form_and_monotone():
    C = 2000.0
    assert cm.xstar_joules(C, 0.9, 0.9) == 0.0          # equal p: resume always
    ws = [cm.xstar_joules(C, 0.93, ps) for ps in (0.6, 0.7, 0.8, 0.9)]
    assert all(a > b for a, b in zip(ws, ws[1:]))       # falls as resume improves
    # brute force check against J definitions
    pr, ps = 0.93, 0.75
    W = cm.xstar_joules(C, pr, ps)
    for w in (W - 1, W + 1):
        Jr, Js = (w + C) / pr, C / ps
        assert (Jr < Js) == (w < W)


def test_depth_optimum_clean_vs_poison():
    law = cm.EnergyLaw(50, 2e-3, 0.0)
    ctx = np.linspace(2e3, 3e4, 20)
    k, _ = cm.best_depth(law, ctx, 12, lambda x: 0.9)
    assert k == 12                                       # clean: resume
    k, _ = cm.best_depth(law, ctx, 12, lambda x: 0.9 if x <= 5 else 0.3)
    assert k == 5                                        # poison at 5: roll back


def test_joules_to_calls_inverse():
    law = cm.EnergyLaw(100, 0, 0)
    assert cm.joules_to_calls(law, [1] * 10, 350) == pytest.approx(3.5)
