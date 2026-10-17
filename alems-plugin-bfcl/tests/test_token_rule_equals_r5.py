"""
token_rule's own computation equals R5's predict_uncached_tokens (coordinator, Oct 17).

Twenty inputs from the real parameters: fitted classes plus an unknown class
(_default fallback), all options, several call counts. Skipped until R5
commits predict_uncached_tokens; after that R6's copy may be dropped.
"""

import itertools
import os

import pytest

from alems_plugin_bfcl.decider import predict as R5
from alems_plugin_bfcl.decider.token_rule import OPTIONS, TokenRule

PARAMS = os.environ.get("ALEMS_RAF_CTRL_PARAMS",
                        "/home/dpani/sandboxes/raf-mlsys-2027/raf_e3/controller_params.json")


def _inputs(params):
    classes = sorted(c for c in params.get("classes", {}) if c != "_default") + ["unknown_class"]
    combos = itertools.product(classes, OPTIONS, (0, 2, 5, 9, 14))
    return list(combos)[::max(1, len(classes) * len(OPTIONS) * 5 // 20)][:20]


@pytest.mark.skipif(not callable(getattr(R5, "predict_uncached_tokens", None)),
                    reason="R5 predict_uncached_tokens not committed yet")
@pytest.mark.skipif(not os.path.exists(PARAMS), reason="R5 params file not present")
def test_r6_copy_equals_r5_function():
    params = R5.load_params(PARAMS)
    inputs = _inputs(params)
    assert len(inputs) == 20
    for cls, opt, calls in inputs:
        mine = TokenRule._from_params(params, cls, opt, calls)
        theirs = R5.predict_uncached_tokens(params, cls, opt, calls)
        assert (mine is None) == (theirs is None), (cls, opt, calls)
        if mine is not None:
            assert mine == pytest.approx(theirs, rel=1e-12), (cls, opt, calls)
