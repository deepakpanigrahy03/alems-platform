"""
laya_p: p from laya-serve (R7), cost and sampling stream from beta_p.

The texts are rendered by the adapter with laya_text v3 (one text per
option) and passed in; this module only sends them. A response is accepted
only if it is calibrated, covers every option, and was produced by a
checkpoint trained on the same template version (coordinator: E3 needs a v3
round 2 checkpoint). Any problem raises LayaUnavailable; the fail closed
wrapper then uses beta_p and records the reason.
"""

import json
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..laya_text import TEMPLATE_VERSION
from .base import Decision, FailureState
from .beta_p import BetaP

TIMEOUT_S = 2.0
IO_SCHEMA = "1.1.0"


class LayaUnavailable(Exception):
    """Raised for timeout, connection error, bad status, bad schema or template mismatch."""


def _post_json(url: str, body: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with opener.open(req, timeout=timeout_s) as resp:
        if int(resp.status) != 200:
            raise LayaUnavailable("status %s" % resp.status)
        return json.loads(resp.read().decode())


class LayaP:
    name = "laya_p"

    def __init__(self, base: BetaP, url: str,
                 post: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
        self.base = base
        self.url = url.rstrip("/") + "/v1/systemone"
        self.post = post or _post_json

    def _p(self, opts: List[str], texts: Dict[str, str]) -> Dict[str, float]:
        missing_text = [o for o in opts if o not in texts]
        if missing_text:
            raise LayaUnavailable("no text for options %s" % missing_text)
        body = {"schema_version": IO_SCHEMA, "template_version": TEMPLATE_VERSION,
                "items": [{"option": o, "text": texts[o]} for o in opts]}
        try:
            resp = self.post(self.url, body, TIMEOUT_S)
        except LayaUnavailable:
            raise
        except Exception as exc:
            raise LayaUnavailable("%s: %s" % (type(exc).__name__, exc))
        if resp.get("template_version") != TEMPLATE_VERSION:
            raise LayaUnavailable("template mismatch: checkpoint %r, texts %r"
                                  % (resp.get("template_version"), TEMPLATE_VERSION))
        if not resp.get("calibration"):
            raise LayaUnavailable("uncalibrated response")
        p = resp.get("p") or {}
        missing = [o for o in opts if o not in p]
        if missing:
            raise LayaUnavailable("missing options %s" % missing)
        out = {o: float(p[o]) for o in opts}
        if any(not 0.0 <= v <= 1.0 for v in out.values()):
            raise LayaUnavailable("p out of range")
        return out

    def decide(self, state: FailureState, options: List[str],
               counts: Optional[Dict[str, Any]] = None, seed: int = 0,
               texts: Optional[Dict[str, str]] = None) -> Decision:
        opts = self.base.candidates(state, options)
        return self.base.decide(state, options, counts=counts, seed=seed,
                                p_override=self._p(opts, texts or {}), name=self.name)
