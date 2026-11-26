"""
Minimal HTTP client for OpenAI compatible chat servers (standard library only).

Capabilities (/tokenize with chat messages, ignore_eos, cached token
reporting, prefix cache reset) are detected per endpoint and recorded,
never assumed.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _request(url, body, timeout_s):
    # type: (str, Optional[Dict[str, Any]], float) -> Any
    """Open a POST request; caller closes the response."""
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout_s)


def post_json(url, body, timeout_s):
    # type: (str, Optional[Dict[str, Any]], float) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[str]]
    """POST and parse the JSON reply; returns (http status, data, error)."""
    try:
        with _request(url, body, timeout_s) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, "http %s" % exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, None, str(exc)


def chat_messages(text):
    # type: (str) -> list
    """One user message; the server applies its own chat template."""
    return [{"role": "user", "content": text}]


def tokenize_count(endpoint, model, text, timeout_s):
    # type: (str, str, str, float) -> Optional[int]
    """Token count of the templated chat input via /tokenize; None if unsupported."""
    body = {"model": model, "messages": chat_messages(text), "add_generation_prompt": True}
    status, data, _ = post_json(endpoint + "/tokenize", body, timeout_s)
    if status != 200 or not isinstance(data, dict):
        return None
    count = data.get("count")
    return int(count) if isinstance(count, int) else None


def probe_prompt_tokens(endpoint, model, text, timeout_s):
    # type: (str, str, str, float) -> Optional[int]
    """Prompt tokens reported by a 1 token completion (fallback when /tokenize is absent)."""
    body = {"model": model, "messages": chat_messages(text), "max_tokens": 1,
            "temperature": 0, "stream": False}
    status, data, _ = post_json(endpoint + "/v1/chat/completions", body, timeout_s)
    usage = (data or {}).get("usage") or {}
    pt = usage.get("prompt_tokens")
    return int(pt) if status == 200 and isinstance(pt, int) else None


def detect_capabilities(endpoint, model, ignore_eos_mode, timeout_s):
    # type: (str, str, str, float) -> Dict[str, Any]
    """
    Probe the endpoint once, before any measured step.
    ignore_eos is probed only in mode 'auto' or 'true'; a 200 reply to a
    request carrying it counts as support (servers that reject unknown fields fail).
    """
    caps = {"tokenize": tokenize_count(endpoint, model, "hello", timeout_s) is not None}
    caps["ignore_eos"] = False
    if ignore_eos_mode in ("auto", "true"):
        body = {"model": model, "messages": chat_messages("hello"), "max_tokens": 1,
                "temperature": 0, "stream": False, "ignore_eos": True}
        status, _, _ = post_json(endpoint + "/v1/chat/completions", body, timeout_s)
        caps["ignore_eos"] = status == 200
    return caps


def reset_prefix_cache(endpoint, timeout_s):
    # type: (str, float) -> bool
    """POST /reset_prefix_cache (vLLM dev mode); True when the server accepted it."""
    status, _, _ = post_json(endpoint + "/reset_prefix_cache", None, timeout_s)
    return status == 200


def prefix_cache_counters(endpoint, queries_metric, hits_metric, timeout_s):
    # type: (str, str, str, float) -> Tuple[Optional[float], Optional[float]]
    """GET /metrics; (queried tokens, hit tokens) summed over label sets, None when not found."""
    try:
        with urllib.request.urlopen(endpoint + "/metrics", timeout=timeout_s) as resp:
            text = resp.read().decode()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("metrics read failed: %s", exc)
        return None, None
    return metric_sum(text, queries_metric), metric_sum(text, hits_metric)


def metric_sum(text, name):
    # type: (str, str) -> Optional[float]
    """Sum of one Prometheus metric over all its label sets; None when the metric is absent."""
    total, seen = 0.0, False
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "}" in line:
            head, rest = line.rsplit("}", 1)
            metric, fields = head.split("{", 1)[0], rest.split()
        else:
            parts = line.split()
            metric, fields = parts[0], parts[1:]
        if metric != name or not fields:
            continue
        try:
            total += float(fields[0])
            seen = True
        except ValueError:
            continue
    return total if seen else None


def parse_sse(line):
    # type: (bytes) -> Optional[Dict[str, Any]]
    """One server sent event line to a dict; None for keep alives, [DONE] and noise."""
    s = line.decode("utf-8", "replace").strip()
    if not s.startswith("data:"):
        return None
    payload = s[5:].strip()
    if payload == "[DONE]":
        return None
    try:
        ev = json.loads(payload)
    except ValueError:
        logger.debug("unparsable stream line: %r", payload[:80])
        return None
    return ev if isinstance(ev, dict) else None


def _absorb_choice(st, ch, now_ns):
    # type: (Dict[str, Any], Dict[str, Any], int) -> None
    """Update stream state from one choice: first content time, finish and stop reason."""
    content = (ch.get("delta") or {}).get("content")
    if content and st["first_ns"] is None:
        st["first_ns"] = now_ns
    if not ch.get("finish_reason"):
        return
    st["finish_reason"] = ch["finish_reason"]
    if "stop_reason" in ch:
        # recorded raw: some servers name the stop token or string here
        st["stop_reason_reported"] = True
        st["stop_reason"] = ch["stop_reason"]


def absorb(st, ev, now_ns):
    # type: (Dict[str, Any], Dict[str, Any], int) -> None
    """Update stream state from one event (usage arrives in the final chunk)."""
    if ev.get("usage"):
        st["usage"] = ev["usage"]
    for ch in ev.get("choices") or []:
        _absorb_choice(st, ch, now_ns)


def new_state():
    # type: () -> Dict[str, Any]
    """Empty stream state; every unknown stays None, never 0."""
    return {"status": "ok", "error": None, "t0_ns": time.time_ns(), "first_ns": None,
            "end_ns": None, "finish_reason": None, "stop_reason": None,
            "stop_reason_reported": False, "usage": None}


def stream_chat(endpoint, model, text, max_tokens, seed, ignore_eos, timeout_s):
    # type: (str, str, str, int, int, bool, float) -> Dict[str, Any]
    """One streaming chat request at temperature 0; returns the stream state."""
    body = {"model": model, "messages": chat_messages(text), "max_tokens": int(max_tokens),
            "temperature": 0, "seed": int(seed), "stream": True,
            "stream_options": {"include_usage": True}}
    if ignore_eos:
        body["ignore_eos"] = True
    st = new_state()
    try:
        with _request(endpoint + "/v1/chat/completions", body, timeout_s) as resp:
            for raw in resp:
                ev = parse_sse(raw)
                if ev is not None:
                    absorb(st, ev, time.time_ns())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        st.update(status="failed", error=str(exc))
    st["end_ns"] = time.time_ns()
    return st
