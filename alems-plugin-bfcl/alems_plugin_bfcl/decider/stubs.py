"""
Stub parameters until R5 delivers controller_params.json (same schema).

Values are placeholders for wiring tests only and are marked as such in
params_version, so a run on stub parameters is visible in every decision.
"""

from typing import Any, Dict

from .base import CACHES, DEPTHS


def stub_params() -> Dict[str, Any]:
    opts = {"%s_%s" % (d, c): {"alpha": 1.0, "beta": 1.0}
            for d in DEPTHS for c in CACHES}
    return {"schema_version": "1.0.0", "params_version": "STUB_NOT_FOR_E3",
            "data_tag": "STUB", "platform": "none", "model": "none",
            "work_unit": "calls",
            "energy_law": {"a": {"value": 0.0}, "b": {"value": 0.0}, "d": {"value": 0.0}},
            "classes": {"default": opts},
            "x_star_calls": {"value": 0.0}, "budget": {"value_j": 1e12}}
