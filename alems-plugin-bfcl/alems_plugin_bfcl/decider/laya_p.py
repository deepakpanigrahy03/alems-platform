"""
laya_p: p from laya-serve (R7), cost and sampling stream from beta_p (design v2).

Any problem raises LayaUnavailable; the fail closed wrapper then uses
beta_p. Text sent per option carries no energy numbers and no task id.
Uncalibrated responses are rejected (laya_p_io schema).
"""

import json
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .base import Decision, FailureState
from .beta_p import BetaP

TIMEOUT_S = 2.0


class LayaUnavailable(Exception):
    """Raised for timeout, connection error, bad status or bad schema."""


def _post_json(url: str, body: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with opener.open(req, timeout=timeout_s) as resp:
        if int(resp.status) != 200:
            raise LayaUnavailable("status %s" % resp.status)
        return json.loads(resp.read().decode())


def option_text(state: FailureState, opt: str) -> str:
    """R5 6.7 input fields only."""
    return "\n".join([
        "failure_class: %s" % state.failure_class,
        "failure_turn: %d" % state.turns_done,
        "option: %s" % opt,
        "attempt: %d" % state.attempt,
        "error: %s" % state.error_text,
    ] + ["turn: %s" % t for t in state.last_turns])


class LayaP:
    name = "laya_p"

    def __init__(self, base: BetaP, url: str,
                 post: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
        self.base = base
        self.url = url.rstrip("/") + "/v1/systemone"
        self.post = post or _post_json

    def _p(self, state: FailureState, opts: List[str]) -> Dict[str, float]:
        body = {"schema_version": "1.0.0",
                "items": [{"option": o, "text": option_text(state, o)} for o in opts]}
        try:
            resp = self.post(self.url, body, TIMEOUT_S)
        except LayaUnavailable:
            raise
        except Exception as exc:
            raise LayaUnavailable("%s: %s" % (type(exc).__name__, exc))
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
               counts: Optional[Dict[str, Any]] = None, seed: int = 0) -> Decision:
        opts = self.base.candidates(state, options)
        return self.base.decide(state, options, counts=counts, seed=seed,
                                p_override=self._p(state, opts), name=self.name)
