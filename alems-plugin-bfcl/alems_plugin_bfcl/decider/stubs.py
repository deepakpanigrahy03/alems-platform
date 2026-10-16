"""
Stub parameters until R5 delivers controller_params.json (R5 layout, Oct 16).

Values are placeholders for wiring tests only and are marked as such in
params_version, so a run on stub parameters is visible in every decision.
Only the pooled _default class exists, with no energy fit.
"""

from typing import Any, Dict

from .base import CACHES, DEPTHS


def stub_params() -> Dict[str, Any]:
    opts = {"%s_%s" % (d, c): {"alpha": 1.0, "beta": 1.0}
            for d in DEPTHS for c in CACHES}
    return {"schema_version": "1.1.0", "params_version": "STUB_NOT_FOR_E3",
            "data_tag": "STUB", "platform": "none", "model": "none",
            "work_unit": "calls",
            "classes": {"_default": {"options": opts}}}
