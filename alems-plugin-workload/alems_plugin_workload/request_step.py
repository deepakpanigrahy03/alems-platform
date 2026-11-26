"""
request_profile step kind: seeded, token sized chat requests, one at a time.

prepare()   before the bracket's pre idle: detect endpoint capabilities and
            build and size every prompt (no measured work).
setup()     outside the measured interval: optional prefix cache reset.
measure()   inside the core measurement window: send requests back to back
            until n_requests are done or duration_s has passed.
A failed request fails the step (StepFailed), so no partial step is stored
as if complete and no step is written as zero energy.
"""
import logging
import time
from typing import Any, Dict, List

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from alems_plugin_workload import PROFILE_SPEC, __version__
from alems_plugin_workload import client
from alems_plugin_workload.outcome import (prefix_cache_results, request_outcome, request_results,
                                           step_summary)
from alems_plugin_workload.prompts import make_prompt, size_prompt, within

logger = logging.getLogger(__name__)
_ROUNDS = {}  # type: Dict[str, int]  # prepare calls per (profile, seed): new prompts every cycle


def _next_round(params):
    # type: (Dict[str, Any]) -> int
    """Deterministic round number, so each cycle gets fresh but reproducible prompts."""
    key = "%s/%s" % (params["profile"], params["seed"])
    _ROUNDS[key] = _ROUNDS.get(key, 0) + 1
    return _ROUNDS[key]


def _counter(params, caps):
    # type: (Dict[str, Any], Dict[str, Any]) -> Any
    """Token counter of the templated chat input: /tokenize, else a 1 token probe."""
    ep, model, to = params["endpoint"], params["model"], float(params["timeout_s"])
    if caps["tokenize"]:
        return lambda text: client.tokenize_count(ep, model, text, to)
    return lambda text: client.probe_prompt_tokens(ep, model, text, to)


def build_prompts(params, caps):
    # type: (Dict[str, Any], Dict[str, Any]) -> List[Dict[str, Any]]
    """Size every prompt to prompt_tokens within sizing_tolerance_pct; raises on failure."""
    rnd, count = _next_round(params), _counter(params, caps)
    target, tol = int(params["prompt_tokens"]), float(params["sizing_tolerance_pct"])
    prompts = []
    for i in range(int(params["n_requests"])):
        build = (lambda n, i=i: make_prompt(params["seed"], rnd, i, n))
        text, tokens, its = size_prompt(build, count, target, tol, int(params.get("max_sizing_iter", 8)))
        if not within(tokens, target, tol):
            raise ValueError("prompt %d sized to %s tokens, target %d" % (i, tokens, target))
        prompts.append({"text": text, "sized_tokens": tokens, "iterations": its})
    return prompts


class RequestProfileStep(StepKindABC):
    """BASE, PREFILL or DECODE style request profile against an OpenAI compatible endpoint."""

    KIND = "request_profile"
    ALEMS_PLUGIN_META = {"name": "workload", "version": __version__, "profile_spec": PROFILE_SPEC}
    PARAMS = {
        "profile": {"type": "string", "required": True},
        "profile_version": {"type": "string", "required": True},
        "prompt_tokens": {"type": "number", "unit": "tokens", "required": True},
        "output_tokens": {"type": "number", "unit": "tokens", "required": True},
        "n_requests": {"type": "number", "required": True},
        "duration_s": {"type": "number", "unit": "s"},
        "endpoint": {"type": "string", "required": True},
        "model": {"type": "string", "required": True},
        "seed": {"type": "number", "required": True},
        "tolerance_pct": {"type": "number", "unit": "%", "required": True},
        "sizing_tolerance_pct": {"type": "number", "unit": "%", "required": True},
        "ignore_eos": {"type": "string", "choices": ["auto", "true", "false"], "required": True},
        "reset_prefix_cache": {"type": "string", "choices": ["auto", "false"]},
        "timeout_s": {"type": "number", "unit": "s", "required": True},
        "max_sizing_iter": {"type": "number"},
        "prefix_cache_queries_metric": {"type": "string"},
        "prefix_cache_hits_metric": {"type": "string"},
    }

    def prepare(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Capabilities and sized prompts; errors are returned, never raised."""
        try:
            caps = client.detect_capabilities(step["endpoint"], step["model"],
                                              step["ignore_eos"], float(step["timeout_s"]))
            return {"caps": caps, "prompts": build_prompts(step, caps), "error": None}
        except Exception as exc:  # prepare must not break the plan; is_available reports it
            logger.warning("request_profile prepare failed: %s", exc)
            return {"caps": {}, "prompts": [], "error": str(exc)}

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        """Prepared, prompts sized, and ignore_eos present when the plan requires it."""
        prep = ctx.get("prepared")
        if not prep:
            return Availability(False, "not_prepared", "prepare() did not run for this bracket")
        if prep.get("error"):
            return Availability(False, "prepare_failed", prep["error"])
        if step["ignore_eos"] == "true" and not prep["caps"].get("ignore_eos"):
            return Availability(False, "ignore_eos_unsupported", step["endpoint"])
        return Availability(True)

    def setup(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """Reset the prefix cache outside the measured interval when asked and supported."""
        caps = ctx["prepared"]["caps"]
        caps["prefix_cache_reset"] = None  # unknown unless attempted
        if step.get("reset_prefix_cache", "false") == "auto":
            caps["prefix_cache_reset"] = int(client.reset_prefix_cache(step["endpoint"], float(step["timeout_s"])))

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Send the prepared requests back to back; one failure fails the step."""
        prep = ctx["prepared"]
        caps = dict(prep["caps"], ignore_eos_used=step["ignore_eos"] != "false" and prep["caps"].get("ignore_eos"))
        pc0 = _prefix_counters(step)
        t0 = time.time_ns()
        outcomes = run_requests(step, prep["prompts"], caps, ctx["cancelled"])
        t1 = time.time_ns()
        pc1 = _prefix_counters(step)
        failed = [i for i, o in enumerate(outcomes) if o["status"] != "ok"]
        if not outcomes:
            raise StepFailed("no_requests", "no request was sent")
        if failed:
            raise StepFailed("request_failed", "%d of %d failed, first %d: %s"
                             % (len(failed), len(outcomes), failed[0], outcomes[failed[0]]["error"]))
        res = step_summary(outcomes, step, caps, t0, t1)
        res.update(request_results(outcomes))
        res.update(prefix_cache_results(pc0, pc1))
        return res


def _prefix_counters(params):
    # type: (Dict[str, Any]) -> Any
    """Prefix cache counters when the plan names both metrics; (None, None) otherwise."""
    q, h = params.get("prefix_cache_queries_metric"), params.get("prefix_cache_hits_metric")
    if not q or not h:
        return None, None
    return client.prefix_cache_counters(params["endpoint"], q, h, float(params["timeout_s"]))


def run_requests(params, prompts, caps, cancelled):
    # type: (Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], Any) -> List[Dict[str, Any]]
    """Sequential requests until the pool or duration_s is used up, or the step is cancelled."""
    deadline = time.monotonic() + float(params.get("duration_s") or 1e12)
    outcomes = []
    for i, p in enumerate(prompts):
        if cancelled() or time.monotonic() >= deadline:
            break
        st = client.stream_chat(params["endpoint"], params["model"], p["text"],
                                params["output_tokens"], int(params["seed"]) + i,
                                bool(caps.get("ignore_eos_used")), float(params["timeout_s"]))
        outcomes.append(request_outcome(st, p, params))
    return outcomes
