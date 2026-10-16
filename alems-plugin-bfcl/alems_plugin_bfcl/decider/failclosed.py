"""
Fail closed wrapper (addendum A rule 3, addendum D4).

The primary decider runs in a worker thread with a deadline. Timeout, any
exception or a laya outage returns the beta_p decision with the reason
recorded. A late primary result is discarded, never applied.
"""

import threading
from dataclasses import replace
from typing import List

from .base import Decision, FailureState
from .beta_p import BetaP

DEADLINE_S = 2.5          # laya timeout 2 s plus margin for the thread


class FailClosed:
    def __init__(self, primary, fallback: BetaP, deadline_s: float = DEADLINE_S) -> None:
        self.primary = primary
        self.fallback = fallback
        self.deadline_s = deadline_s
        self.name = getattr(primary, "name", "primary")

    def decide(self, state: FailureState, options: List[str]) -> Decision:
        if self.primary is self.fallback:
            return self.fallback.decide(state, options)
        box = {}

        def run() -> None:
            try:
                box["d"] = self.primary.decide(state, options)
            except Exception as exc:
                box["e"] = exc

        # A fresh daemon thread per call: a hung call is abandoned and can
        # never delay a later decision (a shared one worker pool could).
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self.deadline_s)
        if "d" in box:
            return box["d"]
        if t.is_alive():
            reason = "timeout"
        else:
            exc = box.get("e")
            reason = "%s: %s" % (type(exc).__name__, exc)
        d = self.fallback.decide(state, options)
        return replace(d, fallback_reason=reason)

    def update(self, outcome) -> None:
        upd = getattr(self.primary, "update", None)
        if callable(upd) and self.primary is not self.fallback:
            upd(outcome)
        else:
            self.fallback.update(outcome)
