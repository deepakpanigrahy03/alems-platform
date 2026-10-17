"""
Laya input text, template v3 (owner R6; shared by the decider and R7's export 1.2.0).

One extraction path and one renderer, so the text the controller sends to
laya-serve at decision time is byte identical to the text the export builds
for training (coordinator gate before E3). Changes from v2 (R7):
  1. no point label anywhere (D3: the controller never sees early or late);
  2. character budget only (the plugin has no tokenizer at decision time);
  3. rendering only from the JSON form (context_json), so JSON round trips
     cannot change the text.

    ctx  = failure_context(messages, cls, harness_error)
    js   = context_json(ctx)
    text = laya_text(js, option, attempt)

Standard library only (INV-14).
"""

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

TEMPLATE_VERSION = "v3"
SCHEMA = "raf.failure_context/1"
DECISION = "ear_recovery_success"          # R7 export default decision name
# laya-serve wire (R7, laya_p_io 1.1.0): the checkpoint is registered under
# this model name, and the question text must be byte identical to training.
LAYA_MODEL = DECISION + "_" + TEMPLATE_VERSION
LAYA_QUESTION = ("Will this recovery attempt complete the user's task correctly, "
                 "given the failure and the chosen option?")

# Constants copied from R7 laya_export.py (v2), unchanged.
FIXED_NOTICE = "[notice] The previous step failed with an error and is being retried."
BUDGET_TOTAL = 1024
BUDGET_ERROR = 256
BUDGET_USER = 256
CHARS_PER_TOKEN = 4
TRUNC_MARK = " [...] "


# ── extraction (R7 v2 logic, point removed) ────────────────────────
def content_text(msg: Dict[str, Any]) -> str:
    """Plain text of a message; list contents are joined, None is empty."""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def render_agent(msg: Dict[str, Any]) -> str:
    """Assistant message as text: prose plus tool calls name(args)."""
    text = content_text(msg).strip()
    calls = []
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {})
        calls.append("call %s(%s)" % (fn.get("name", "?"), fn.get("arguments", "")))
    return " ".join(t for t in [text] + calls if t)


def strip_notice(messages: List[Dict[str, Any]]) -> bool:
    """Remove the fixed notice line from every message, in place; True if found."""
    found = False
    for msg in messages:
        text = msg.get("content")
        if not isinstance(text, str) or FIXED_NOTICE not in text:
            continue
        found = True
        msg["content"] = text.replace("\n" + FIXED_NOTICE, "").replace(FIXED_NOTICE, "")
    return found


def find_error(messages: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Last tool error the agent saw: (text, source) with source tool_json, tool_text or none."""
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        text = content_text(msg)
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and "error" in data:
            return "%s: %s" % (msg.get("name", "tool"), data["error"]), "tool_json"
        if '"error"' in text:
            return "%s: %s" % (msg.get("name", "tool"), text), "tool_text"
    return "none", "none"


def failure_context(messages: List[Dict[str, Any]], cls: str,
                    harness_error: Optional[str]) -> Dict[str, Any]:
    """
    The failure context record: what the controller knows at the failure.

    cls is the observable class. The error comes from the agent context when
    present, else from the harness ("harness: <message>").
    """
    msgs = [m for m in copy.deepcopy(messages or []) if isinstance(m, dict)]
    notice = strip_notice(msgs)
    error, source = find_error(msgs)
    if source == "none" and harness_error:
        error, source = "harness: " + str(harness_error), "attempt1_error_message"
    users = [content_text(m) for m in msgs if m.get("role") == "user"]
    agents = [render_agent(m) for m in msgs if m.get("role") == "assistant"]
    return {"schema": SCHEMA, "cls": cls, "error": error, "error_source": source,
            "last_user": users[-1] if users else "none",
            "last_agent": agents[-2:],             # oldest first
            "notice_stripped": notice}


def context_json(ctx: Dict[str, Any]) -> str:
    """Canonical JSON form; the only input laya_text accepts."""
    return json.dumps(ctx, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ── rendering (character budget only) ──────────────────────────────
def _count(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _cut(text: str, limit: int) -> str:
    """Keep head and tail within limit tokens (R7 v2 rule, character mode)."""
    if limit <= 0:
        return ""
    if _count(text) <= limit:
        return text
    keep = limit * CHARS_PER_TOKEN
    while keep > 0:
        half = keep // 2
        cand = text[:half] + TRUNC_MARK + text[-half:]
        if _count(cand) <= limit:
            return cand
        keep = int(keep * 0.9)
    return ""


def laya_text(ctx_json: str, option: str, attempt: int) -> str:
    """Rendered input for one (failure, option); never carries energy, tokens, task, point."""
    ctx = json.loads(ctx_json)
    depth, _, cache = option.partition("_")
    header = ("decision: %s\nclass: %s | option: %s,%s | attempt: %d"
              % (DECISION, ctx["cls"], depth, cache, int(attempt)))
    error = _cut(ctx["error"], BUDGET_ERROR)
    user = _cut(ctx["last_user"], BUDGET_USER)
    fixed = header + "\nerror: " + error + "\nlast_user: " + user + "\nlast_agent: "
    agent_src = " || ".join(ctx["last_agent"]) if ctx["last_agent"] else "none"
    return fixed + _cut(agent_src, BUDGET_TOTAL - _count(fixed))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
