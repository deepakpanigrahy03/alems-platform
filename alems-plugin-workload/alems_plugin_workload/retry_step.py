"""
retry_context step kind (Paper 5, P5-C): controlled application level retry
simulation with post generation failure injection, under context growth or
fixed context.

One step runs one arm at one retry count k over n_prompts seeded base prompts.
The base prompts depend only on (seed, index), so every arm and every k of a
session sees the same prompts (matched goals). For each goal:

  baseline      k must be 0: one call with the base prompt.
  fixed         k + 1 calls, each with the base prompt only (context reset).
  growth        k + 1 calls; each retry appends the previous answer and a user
                turn made of retry_note followed by the base prompt restated.
  final_single  one call shaped like growth's last attempt (k prior
                exchanges), with synthetic assistant turns sized to
                output_tokens; matches the maximum per attempt context, not the
                cumulative input processing.

Attempts 1 to k are marked failed by the application after generation has
completed (full output, no interruption); attempt k + 1 is the success, so
every goal succeeds and the denominator is fixed. When reset_prefix_cache is
auto, the server prefix cache is reset before each goal and kept between the
attempts of one goal. Prefix cache counters are read around every attempt
when the plan names both metrics.

Stored per goal (g_NNN.*): window t0_ns and t1_ns, attempts, input and output
token totals. Per attempt (g_NNN.a_N.*): tokens, cached tokens, prefix cache
deltas, timing, outcome. Energy belongs to the step; analysis allocates it to
goal windows. Unknown values are absent (NULL), never 0. A failed request fails
the step.
"""
import logging
import random
import time
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.sequencer import Availability, StepFailed, StepKindABC

from alems_plugin_workload import __version__
from alems_plugin_workload import client
from alems_plugin_workload.outcome import prefix_cache_results, request_outcome
from alems_plugin_workload.prompts import make_prompt, size_prompt, within

logger = logging.getLogger(__name__)
ARMS = ("baseline", "fixed", "growth", "final_single")
FILLER_ROUND = 1   # make_prompt round used for synthetic assistant turns (base prompts use 0)


def attempts_for(arm, k):
    # type: (str, int) -> int
    """Calls per goal: k + 1 for fixed and growth, 1 for baseline and final_single."""
    return k + 1 if arm in ("fixed", "growth") else 1


def messages_for(arm, base, note, prior_outputs):
    # type: (str, str, str, List[str]) -> List[Dict[str, str]]
    """Chat messages of one call: base alone, or base plus prior exchanges (growth shape)."""
    msgs = [{"role": "user", "content": base}]
    if arm in ("baseline", "fixed"):
        return msgs
    for out in prior_outputs:
        msgs.append({"role": "assistant", "content": out})
        msgs.append({"role": "user", "content": note + "\n\n" + base})
    return msgs


def tokenize_messages(endpoint, model, messages, timeout_s):
    # type: (str, str, List[Dict[str, str]], float) -> Optional[int]
    """Token count of templated chat messages via /tokenize; None if unsupported."""
    body = {"model": model, "messages": messages, "add_generation_prompt": True}
    status, data, _ = client.post_json(endpoint + "/tokenize", body, timeout_s)
    count = (data or {}).get("count") if isinstance(data, dict) else None
    return int(count) if status == 200 and isinstance(count, int) else None


def stream_messages(endpoint, model, messages, max_tokens, seed, ignore_eos, timeout_s):
    # type: (str, str, List[Dict[str, str]], int, int, bool, float) -> Tuple[Dict[str, Any], str]
    """One streaming chat call at temperature 0; returns (stream state, generated text)."""
    body = {"model": model, "messages": messages, "max_tokens": int(max_tokens), "temperature": 0,
            "seed": int(seed), "stream": True, "stream_options": {"include_usage": True}}
    if ignore_eos:
        body["ignore_eos"] = True
    st, parts = client.new_state(), []
    try:
        with client._request(endpoint + "/v1/chat/completions", body, timeout_s) as resp:
            for raw in resp:
                ev = client.parse_sse(raw)
                if ev is None:
                    continue
                client.absorb(st, ev, time.time_ns())
                for ch in ev.get("choices") or []:
                    parts.append((ch.get("delta") or {}).get("content") or "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        st.update(status="failed", error=str(exc))
    st["end_ns"] = time.time_ns()
    return st, "".join(parts)


class RetryContextStep(StepKindABC):
    """P5-C retry and context growth arm as a measured sequencer step."""

    KIND = "retry_context"
    ALEMS_PLUGIN_META = {"name": "workload", "version": __version__}
    PARAMS = {
        "arm": {"type": "string", "choices": list(ARMS), "required": True},
        "k": {"type": "number", "required": True},
        "n_prompts": {"type": "number", "required": True},
        "prompt_tokens": {"type": "number", "unit": "tokens", "required": True},
        "output_tokens": {"type": "number", "unit": "tokens", "required": True},
        "retry_note": {"type": "string", "required": True},
        "endpoint": {"type": "string", "required": True},
        "model": {"type": "string", "required": True},
        "seed": {"type": "number", "required": True},
        "tolerance_pct": {"type": "number", "unit": "%", "required": True},
        "sizing_tolerance_pct": {"type": "number", "unit": "%", "required": True},
        "ignore_eos": {"type": "string", "choices": ["true", "false"], "required": True},
        "reset_prefix_cache": {"type": "string", "choices": ["auto", "false"], "required": True},
        "timeout_s": {"type": "number", "unit": "s", "required": True},
        "max_sizing_iter": {"type": "number", "required": True},
        "prefix_cache_queries_metric": {"type": "string"},
        "prefix_cache_hits_metric": {"type": "string"},
    }

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """Arm and k must agree: baseline only at k 0, the others at k 1 or more."""
        errors = super().validate(params)
        arm, k = params.get("arm"), params.get("k")
        if isinstance(k, (int, float)):
            if arm == "baseline" and k != 0:
                errors.append("retry_context: baseline needs k 0")
            if arm in ("fixed", "growth", "final_single") and k < 1:
                errors.append("retry_context: %s needs k of 1 or more" % arm)
        return errors

    def prepare(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Size the base prompts (and fillers for final_single); errors are returned, never raised."""
        try:
            ep, model, to = step["endpoint"], step["model"], float(step["timeout_s"])
            count = lambda text: client.tokenize_count(ep, model, text, to)  # noqa: E731
            if count("probe") is None:
                return {"error": "/tokenize unavailable: prompts cannot be sized", "prompts": []}
            target, tol, its = int(step["prompt_tokens"]), float(step["sizing_tolerance_pct"]), int(step["max_sizing_iter"])
            prompts = []
            for i in range(int(step["n_prompts"])):
                text, tokens, _ = size_prompt(lambda n, i=i: make_prompt(step["seed"], 0, i, n), count, target, tol, its)
                if not within(tokens, target, tol):
                    return {"error": "prompt %d sized to %s tokens, target %d" % (i, tokens, target), "prompts": []}
                fillers = []
                if step["arm"] == "final_single":
                    for j in range(int(step["k"])):
                        ftext, _, _ = size_prompt(lambda n, i=i, j=j: make_prompt(step["seed"], FILLER_ROUND, i * 16 + j, n),
                                                  count, int(step["output_tokens"]), tol, its)
                        fillers.append(ftext)
                prompts.append({"index": i, "text": text, "sized_tokens": tokens, "fillers": fillers})
            order = list(range(len(prompts)))
            random.Random("alems-retry/%s/%s/%s" % (step["seed"], step["arm"], step["k"])).shuffle(order)
            return {"prompts": [prompts[i] for i in order], "error": None}
        except Exception as exc:  # prepare must not break the plan; is_available reports it
            logger.warning("retry_context prepare failed: %s", exc)
            return {"error": str(exc), "prompts": []}

    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        """Prepared without error."""
        prep = ctx.get("prepared")
        if not prep:
            return Availability(False, "not_prepared", "prepare() did not run for this bracket")
        if prep.get("error"):
            return Availability(False, "prepare_failed", prep["error"])
        return Availability(True)

    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        """Run every goal of the arm back to back; one failed call fails the step."""
        res = {}  # type: Dict[str, Any]
        resets, goals = [], 0
        t0 = time.time_ns()
        for g, p in enumerate(ctx["prepared"]["prompts"]):
            if ctx["cancelled"]():
                raise StepFailed("cancelled", "sequencer asked the step to stop")
            if step["reset_prefix_cache"] == "auto":
                resets.append(int(client.reset_prefix_cache(step["endpoint"], float(step["timeout_s"]))))
            res.update(self._goal(step, g, p))
            goals += 1
        t1 = time.time_ns()
        res.update({"arm": (step["arm"], None), "k": (int(step["k"]), None), "n_goals": (goals, None),
                    "attempts_per_goal": (attempts_for(step["arm"], int(step["k"])), None),
                    "seed": (step["seed"], None), "work_t0_ns": (t0, "ns"), "work_t1_ns": (t1, "ns"),
                    "work_s": (round((t1 - t0) / 1e9, 3), "s")})
        if resets:
            res["n_cache_resets_accepted"] = (sum(resets), None)
            res["n_cache_resets_requested"] = (len(resets), None)
        return res

    def _goal(self, step, g, p):
        # type: (Dict[str, Any], int, Dict[str, Any]) -> Dict[str, Any]
        """One goal: its attempts, per attempt keys and the goal window."""
        arm, k = step["arm"], int(step["k"])
        n_att = attempts_for(arm, k)
        prior = list(p["fillers"]) if arm == "final_single" else []
        out, totals = {}, {"in": [], "out": []}
        pre = "g_%03d." % g
        g0 = None
        for a in range(1, n_att + 1):
            msgs = messages_for(arm, p["text"], step["retry_note"], prior)
            pc0 = self._counters(step)
            st, text = stream_messages(step["endpoint"], step["model"], msgs, int(step["output_tokens"]),
                                       int(step["seed"]) * 1000 + p["index"] * 10 + a,
                                       step["ignore_eos"] == "true", float(step["timeout_s"]))
            pc1 = self._counters(step)
            g0 = st["t0_ns"] if g0 is None else g0
            if st["status"] != "ok":
                raise StepFailed("request_failed", "goal %d attempt %d: %s" % (g, a, st.get("error")))
            o = request_outcome(st, {"sized_tokens": p["sized_tokens"] if a == 1 else None}, step)
            o["outcome"] = "success" if a == n_att else "failed_injected"
            o["messages"] = len(msgs)
            ap = "%sa_%d." % (pre, a)
            out.update({ap + key: (val, _UNITS.get(key)) for key, val in o.items() if val is not None})
            out.update({ap + key: v for key, v in prefix_cache_results(pc0, pc1).items()})
            out[ap + "t0_ns"], out[ap + "end_ns"] = (st["t0_ns"], "ns"), (st["end_ns"], "ns")
            totals["in"].append(o.get("prompt_tokens"))
            totals["out"].append(o.get("output_tokens"))
            if arm == "growth":
                prior.append(text)
        out[pre + "prompt_index"] = (p["index"], None)
        out[pre + "attempts"] = (n_att, None)
        out[pre + "t0_ns"], out[pre + "t1_ns"] = (g0, "ns"), (st["end_ns"], "ns")
        for key, vals in (("input_tokens_total", totals["in"]), ("output_tokens_total", totals["out"])):
            if all(v is not None for v in vals):
                out[pre + key] = (sum(vals), "tokens")
        return out

    @staticmethod
    def _counters(step):
        # type: (Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]
        """Prefix cache counters when the plan names both metrics."""
        q, h = step.get("prefix_cache_queries_metric"), step.get("prefix_cache_hits_metric")
        if not q or not h:
            return None, None
        return client.prefix_cache_counters(step["endpoint"], q, h, float(step["timeout_s"]))


_UNITS = {"prompt_tokens_sized": "tokens", "prompt_tokens": "tokens", "output_tokens": "tokens",
          "cached_tokens": "tokens", "ttft_ms": "ms", "total_ms": "ms"}
