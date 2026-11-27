"""
Per request outcome and step summary, as results {key: (value, unit)}.

Rules:
  * Unknown is absent (stored NULL by core), never 0.
  * early_stop: the server reports finish_reason 'stop' before max_tokens.
  * early_eos: 1 only when the server names an EOS token as the stop cause
    (stop_reason in the plan's eos_token_ids); 0 when the full length was
    produced; otherwise unknown. A generic stop is an early stop, not EOS.
  * Energy belongs to the whole step; per request keys carry no energy.
"""
from typing import Any, Dict, List, Optional, Tuple

from alems_plugin_workload.prompts import within

Results = Dict[str, Tuple[Any, Optional[str]]]


def eos_flag(finish, stop_reason, out_tokens, max_tokens, eos_ids):
    # type: (Optional[str], Any, Optional[int], int, List[Any]) -> Optional[int]
    """EOS as the stop cause: 1 identified, 0 ruled out, None unknown."""
    if finish == "length" or (out_tokens is not None and out_tokens >= max_tokens):
        return 0
    if finish != "stop":
        return None
    if eos_ids and stop_reason in eos_ids:
        return 1
    return None


def request_outcome(st, prep, params):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """Flatten one stream state into the stored per request fields."""
    usage = st.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    max_tokens = int(params["output_tokens"])
    out_tokens = usage.get("completion_tokens")
    finish = st.get("finish_reason")
    early_stop = None
    if out_tokens is not None and finish is not None:
        early_stop = int(finish == "stop" and out_tokens < max_tokens)
    first, t0, end = st.get("first_ns"), st["t0_ns"], st.get("end_ns")
    return {
        "status": st["status"], "error": st.get("error"),
        "prompt_tokens_sized": prep.get("sized_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": out_tokens,
        "cached_tokens": details.get("cached_tokens"),
        "finish_reason": finish,
        "stop_reason": st.get("stop_reason") if st.get("stop_reason_reported") else None,
        "early_stop": early_stop,
        "early_eos": eos_flag(finish, st.get("stop_reason"), out_tokens, max_tokens,
                              list(params.get("eos_token_ids") or [])),
        "ttft_ms": (first - t0) / 1e6 if first else None,
        "total_ms": (end - t0) / 1e6 if end else None,
    }


UNITS = {"prompt_tokens_sized": "tokens", "prompt_tokens": "tokens", "output_tokens": "tokens",
         "cached_tokens": "tokens", "ttft_ms": "ms", "total_ms": "ms"}


def request_results(outcomes):
    # type: (List[Dict[str, Any]]) -> Results
    """Keys req_NNN.<field>; None values are left out so core stores nothing (NULL)."""
    out = {}  # type: Results
    for i, o in enumerate(outcomes):
        out.update({"req_%03d.%s" % (i, k): (v, UNITS.get(k))
                    for k, v in o.items() if v is not None})
    return out


def _sum_known(outcomes, key):
    # type: (List[Dict[str, Any]], str) -> Optional[int]
    """Sum of a field over requests that reported it; None when none did."""
    vals = [o[key] for o in outcomes if o.get(key) is not None]
    return sum(vals) if vals else None


def _share(flags):
    # type: (List[Optional[int]]) -> Optional[float]
    """Share of 1 among known flags; None when all are unknown."""
    known = [f for f in flags if f is not None]
    return round(sum(known) / float(len(known)), 4) if known else None


def step_summary(outcomes, params, caps, t0_ns, t1_ns):
    # type: (List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], int, int) -> Results
    """Step level results: counts, target shares, cache reuse, throughput, capabilities."""
    tol = float(params["tolerance_pct"])
    out_total = _sum_known(outcomes, "output_tokens")
    prompt_rep = [o for o in outcomes if o.get("cached_tokens") is not None]
    work_s = (t1_ns - t0_ns) / 1e9
    res = {
        "profile": (params["profile"], None), "profile_version": (params["profile_version"], None),
        "seed": (params["seed"], None), "n_requests": (len(outcomes), None),
        "n_early_stop": (sum(1 for o in outcomes if o.get("early_stop") == 1), None),
        "n_early_eos_identified": (sum(1 for o in outcomes if o.get("early_eos") == 1), None),
        "share_output_within_tol": (_share([within(o.get("output_tokens"), params["output_tokens"], tol) for o in outcomes]), None),
        "share_prompt_within_tol": (_share([within(o.get("prompt_tokens"), params["prompt_tokens"], tol) for o in outcomes]), None),
        "tolerance_pct": (tol, "%"), "work_t0_ns": (t0_ns, "ns"), "work_t1_ns": (t1_ns, "ns"),
        "work_s": (round(work_s, 3), "s"),
        "cap_tokenize": (int(bool(caps.get("tokenize"))), None),
        "cap_ignore_eos_used": (int(bool(caps.get("ignore_eos_used"))), None),
        "cap_prefix_cache_reset": (caps.get("prefix_cache_reset"), None),
        "n_cached_reported": (len(prompt_rep), None),
    }
    res.update(_optional_totals(outcomes, prompt_rep, out_total, work_s))
    return {k: v for k, v in res.items() if v[0] is not None}


def prefix_cache_results(before, after):
    # type: (Tuple[Optional[float], Optional[float]], Tuple[Optional[float], Optional[float]]) -> Results
    """Server prefix cache counter deltas over the measured interval; absent when unknown or reset."""
    dq, dh = _delta(before[0], after[0]), _delta(before[1], after[1])
    res = {}  # type: Results
    if dq is not None:
        res["prefix_cache_queried_tokens"] = (int(dq), "tokens")
    if dh is not None:
        res["prefix_cache_hit_tokens"] = (int(dh), "tokens")
    if dq and dh is not None:
        res["prefix_cache_hit_share"] = (round(dh / dq, 4), None)
    return res


def _delta(a, b):
    # type: (Optional[float], Optional[float]) -> Optional[float]
    """b minus a; None when either is unknown or the counter went backwards (server restart)."""
    if a is None or b is None or b < a:
        return None
    return b - a


def _optional_totals(outcomes, prompt_rep, out_total, work_s):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]], Optional[int], float) -> Results
    """Totals that exist only when the server reported the underlying fields."""
    res = {"output_tokens_total": (out_total, "tokens"),
           "prompt_tokens_total": (_sum_known(outcomes, "prompt_tokens"), "tokens")}
    if out_total is not None and work_s > 0:
        res["throughput_out_tok_s"] = (round(out_total / work_s, 3), "tokens/s")
    cached = _sum_known(prompt_rep, "cached_tokens")
    prompts = _sum_known(prompt_rep, "prompt_tokens")
    if cached is not None and prompts:
        res["cached_share"] = (round(cached / float(prompts), 4), None)
    return res
