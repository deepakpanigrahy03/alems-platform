"""
laya_p: p from laya-serve (R7), cost and sampling stream from beta_p.

Wire (R7, laya_p_io 1.1.0): POST /v1/systemone, one text per call, body
exactly {state, model, questions}; the reply is accepted only if its model
equals LAYA_MODEL (ear_recovery_success_v3), so a checkpoint trained on
another template can never answer. Texts are rendered by the adapter with
laya_text v3. Reasons recorded on fail closed: unreachable, template
mismatch, bad answer (R7 contract).
"""

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..laya_text import DECISION, LAYA_MODEL, LAYA_QUESTION
from .base import Decision, FailureState
from .beta_p import BetaP

TIMEOUT_S = 2.0
DEFAULT_URL = "http://127.0.0.1:8000"


class LayaUnavailable(Exception):
    """Raised with reason unreachable, template mismatch or bad answer."""


def request_body(text: str) -> Dict[str, Any]:
    """Exactly the three keys laya-serve accepts (no others)."""
    return {"state": text, "model": LAYA_MODEL,
            "questions": {DECISION: {"type": "noul", "instructions": LAYA_QUESTION}}}


def _post_json(url: str, body: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # 400, 404, 422: the server does not know this model or body.
        raise LayaUnavailable("template mismatch: HTTP %s" % exc.code)
    except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as exc:
        raise LayaUnavailable("unreachable: %s" % exc)


def p_yes(resp: Dict[str, Any]) -> float:
    """p from one reply; raises LayaUnavailable on another model or a bad answer."""
    if resp.get("model") != LAYA_MODEL:
        raise LayaUnavailable("template mismatch: model %r" % resp.get("model"))
    try:
        v = float(resp["answers"][DECISION]["noul"])
    except (KeyError, TypeError, ValueError):
        raise LayaUnavailable("bad answer: no noul")
    if not 0.0 <= v <= 1.0:
        raise LayaUnavailable("bad answer: noul %r out of range" % v)
    return v


class LayaP:
    name = "laya_p"

    def __init__(self, base: BetaP, url: str = DEFAULT_URL,
                 post: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
        self.base = base
        self.url = url.rstrip("/") + "/v1/systemone"
        self.post = post or _post_json

    def _p(self, opts: List[str], texts: Dict[str, str]) -> Dict[str, float]:
        missing = [o for o in opts if o not in texts]
        if missing:
            raise LayaUnavailable("bad answer: no text for options %s" % missing)
        out = {}
        for o in opts:                            # one text per call (R7)
            try:
                resp = self.post(self.url, request_body(texts[o]), TIMEOUT_S)
            except LayaUnavailable:
                raise
            except Exception as exc:
                raise LayaUnavailable("unreachable: %s: %s" % (type(exc).__name__, exc))
            out[o] = p_yes(resp)
        return out

    def decide(self, state: FailureState, options: List[str],
               counts: Optional[Dict[str, Any]] = None, seed: int = 0,
               texts: Optional[Dict[str, str]] = None) -> Decision:
        opts = self.base.candidates(state, options)
        return self.base.decide(state, options, counts=counts, seed=seed,
                                p_override=self._p(opts, texts or {}), name=self.name)
