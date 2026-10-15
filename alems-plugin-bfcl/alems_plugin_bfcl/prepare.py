"""
Pre attempt preparation for RAF LH 01 (cache resets, tool state restore).

The pilot driver installs one PreAttempt per goal execution as
executor.pre_attempt_hook. execute_goal calls it immediately before
harness.run_agentic, which is before t0, so nothing here is inside a
measurement window (C-BND, design section 6).

Design 4.2 cache rules:
    attempt 1, every policy      reset
    attempt 2, cold policies     reset again
    attempt 2, warm policies     cache preserved
Design 4.1 restore rules (attempt 2 only):
    FR  tool state back to the task's initial state
    SR  tool state back to the snapshot before failed tool call k
    RB  tool state back to the start of the suspect turn (R2 spec 2.1)

A failed reset never raises: it records CACHE_RESET_FAILED so the goal
execution is classified invalid afterwards (C-VAL), and the run proceeds
unchanged. Records are kept on executor._raf_prepare keyed by attempt and
returned by the adapter as a raf.prepare annotation.
"""

import time
import urllib.request

from core.telemetry import vllm_metrics   # server cache statistics, read between attempts
from typing import Any, Callable, Dict, Optional

CACHE_RESET_FAILED = "CACHE_RESET_FAILED"
RESTORE_MISMATCH = "RESTORE_MISMATCH"
RB_NOT_APPLICABLE = "RB_NOT_APPLICABLE"
POLICIES = ("B0", "FR_cold", "FR_warm", "SR_cold", "SR_warm",
            "RB_cold", "RB_warm")


def _http_post(url: str, timeout_s: float) -> int:
    """POST with an empty body; proxies bypassed (lab proxy breaks localhost)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=b"", method="POST")
    with opener.open(req, timeout=timeout_s) as resp:
        return int(resp.status)


DEFAULT_RESET_PATH = "/reset_prefix_cache"   # vLLM dev mode route (GN100)


def reset_url(base_url: str, path: Optional[str] = None) -> str:
    """Model binding base URL ends in /v1; the reset route lives at the root.

    path is the model binding cache_reset.path when declared (llama.cpp
    server: /slots/0?action=erase); absent, the vLLM route is used.
    """
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root + (path or DEFAULT_RESET_PATH)


class PreAttempt:
    """Callable hook: hook(attempt_number). One instance per goal execution."""

    def __init__(self, executor: Any, base_url: str, policy: str,
                 post: Optional[Callable[[str, float], int]] = None,
                 timeout_s: float = 10.0,
                 reset_path: Optional[str] = None,
                 decide: Optional[Callable[[int, Any], Optional[str]]] = None,
                 metrics: Optional[Callable[[str], Optional[Dict[str, float]]]] = None
                 ) -> None:
        if policy not in POLICIES:
            raise ValueError("unknown policy %r" % policy)
        self.executor = executor
        self.policy = policy
        self.url = reset_url(base_url, reset_path)
        self.base_url = base_url
        self.read_metrics = metrics or vllm_metrics.read
        self.timeout_s = timeout_s
        # Tests inject a fake; production uses urllib.
        self.post = post or _http_post
        # Decision seam (R2 8.1, R6 addendum A): called before every attempt
        # from 2 on, outside windows, with the executor; returns the policy for
        # that attempt or None (keep). Deciders are plugins loaded by core.
        self.decide = decide
        # The adapter reads depth as "FR" or "SR" from _raf_context["policy"];
        # set it here so the policy has one source for prepare and adapter.
        ctx = dict(getattr(executor, "_raf_context", None) or {})
        if policy != "B0":
            ctx["policy"] = policy[:2]
            ctx["cache"] = policy[3:]
        executor._raf_context = ctx

    # ── public ──────────────────────────────────────────────────────
    def __call__(self, attempt_number: int) -> Dict[str, Any]:
        ex = self.executor
        # execute_goal sets _current_attempt only when it holds the injector;
        # the pilot keeps the injector out of execute_goal (post harness path),
        # so attempt identity for the adapter is set here.
        ex._current_attempt = attempt_number
        if attempt_number >= 2 and self.decide is not None:
            t_dec = time.perf_counter_ns()
            chosen = self.decide(attempt_number, ex)
            self._switch(chosen)
            # Decision cost is logged per decision (R6 addendum A rule 1).
            ex._raf_decisions = dict(getattr(ex, "_raf_decisions", None) or {})
            ex._raf_decisions[attempt_number] = {
                "chosen": chosen, "policy": self.policy,
                "ns": time.perf_counter_ns() - t_dec}
        rec = {"attempt": attempt_number, "policy": self.policy,
               "resets": [], "restore": None, "validity": [],
               # Before any reset: the cache as the previous attempt left it.
               "cache_metrics": self.read_metrics(self.base_url)}
        if self._needs_reset(attempt_number):
            rec["resets"].append(self._reset())
        if attempt_number >= 2 and self.policy != "B0":
            rec["restore"] = self._restore()
        if any(not r["ok"] for r in rec["resets"]):
            rec["validity"].append(CACHE_RESET_FAILED)
        if rec["restore"] is not None and rec["restore"].get("ok") is False:
            rec["validity"].append(RESTORE_MISMATCH)
        if rec["restore"] is not None and rec["restore"].get("not_applicable"):
            # RB with no completed turn before the suspect turn: executed as
            # FR plus notice, excluded from RB statistics, never invalid.
            rec["validity"].append(RB_NOT_APPLICABLE)
        store = dict(getattr(ex, "_raf_prepare", None) or {})
        store[attempt_number] = rec
        ex._raf_prepare = store
        return rec

    # ── internals ───────────────────────────────────────────────────
    def _switch(self, policy: Optional[str]) -> None:
        """Adopt a new recovery policy for this and later attempts."""
        if not policy or policy == self.policy:
            return
        if policy not in POLICIES or policy == "B0":
            raise ValueError("unknown escalation policy %r" % policy)
        self.policy = policy
        # The adapter reads depth and cache from the context, as at init.
        ctx = dict(getattr(self.executor, "_raf_context", None) or {})
        ctx["policy"] = policy[:2]
        ctx["cache"] = policy[3:]
        self.executor._raf_context = ctx

    def _needs_reset(self, attempt_number: int) -> bool:
        if attempt_number <= 1:
            return True                      # every policy, design 4.2
        return self.policy.endswith("_cold")  # warm keeps the cache

    def _reset(self) -> Dict[str, Any]:
        t0 = time.perf_counter_ns()
        status, err = None, None
        try:
            status = self.post(self.url, self.timeout_s)
        except Exception as exc:  # never raise: invalidity is recorded instead
            err = "%s: %s" % (type(exc).__name__, exc)
        return {"status": status, "ok": status == 200, "error": err,
                "ns": time.perf_counter_ns() - t0, "wall_ns": time.time_ns()}

    def _restore(self) -> Dict[str, Any]:
        session = getattr(self.executor, "_bfcl_session", None)
        f = getattr(session, "failure", None) if session is not None else None
        if f is None:
            # No recorded failure point (attempt 1 did not fail by injection);
            # the adapter falls back to FR inside its window as before.
            return {"skipped": "no_failure"}
        prov = session.provider
        depth = self.policy[:2]
        t0 = time.perf_counter_ns()
        before = prov.state_hash()
        if depth == "FR":
            prov.restore_initial()
            expected = session.initial_hash
        elif depth == "RB":
            return self._restore_rollback(session, f, before, t0)
        else:
            k = f["provider_calls"] + 1      # ordinal of the failed tool call
            if prov.env.calls_made >= k:
                prov.restore_before(k)
            expected = f["state_hash"]
        after = prov.state_hash()
        rec = {"depth": depth, "state_hash_before": before,
               "state_hash_after": after, "expected_hash": expected,
               "ok": after == expected, "ns": time.perf_counter_ns() - t0}
        # The adapter reads this and skips its own in window restore.
        session.prepared_restore = rec
        return rec

    def _restore_rollback(self, session: Any, f: Dict[str, Any],
                          before: str, t0: int) -> Dict[str, Any]:
        """RB: tool state at the start of suspect turn t_s (spec 2.1)."""
        prov = session.provider
        t = int(f["turn"])
        start = int(f["turn_starts"][t])     # calls made before turn t opened
        if prov.env.calls_made > start:
            prov.restore_before(start + 1)   # state before call start + 1
        # Turns from t on are redone; begin_turn(t) must be legal again.
        prov.turn_starts = list(f["turn_starts"][:t])
        after = prov.state_hash()
        # Check by call count: computing a hash per turn in attempt 1 would
        # add cost inside every measurement window.
        ok = len(prov.env.call_log) == start
        rec = {"depth": "RB", "turn": t, "calls_kept": start,
               "state_hash_before": before, "state_hash_after": after,
               "expected_hash": None, "ok": ok, "not_applicable": t == 0,
               "ns": time.perf_counter_ns() - t0}
        session.prepared_restore = rec
        return rec
