"""
RAF context sweep probe (R2, Oct 20): energy per LLM call versus prompt size, uncached and cached.

Three plugin pieces, loaded by name through entry points (core never imports this module):

    alems.frameworks         raf_sweep   SweepAdapter     one completion call, inside the window
    alems.harness.prepare    sweep       make_prepare     prompt build, cache reset or warm call, before t0
    alems.harness.validity   sweep       classify         output length, prompt length, cache mode

Tasks are named raf_sweep_<size>_<mode> (mode: uncached or cached) in config/tasks.yaml with
framework raf_sweep. Every sweep goal is a B0 goal (no injector). The prepare hook reads the goal's plan from
executor._raf_plan (one additive line in scheduled_runner.run_goal) and hands the prompt to the framework on
executor._sweep_prompt. Unreported usage stays None, never 0 (MIC-1).
"""

import hashlib
import json
import random
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from alems_sdk.frameworks import FrameworkAdapterABC, FrameworkResult

OUT_TOKENS = 64                  # fixed output length, every call (ignore_eos)
PROMPT_TOLERANCE = 2             # tokens; the binary search lands at or just below the target
UNCACHED_MAX_CACHED = 32         # an uncached call may still match a few leading tokens
DEFAULT_RESET_PATH = "/reset_prefix_cache"
HTTP_TIMEOUT_S = 600
WORDS = ("energy cache prefix token model agent tool state turn recovery measure baseline sample "
         "window counter domain package input request reply").split()
MODES = ("uncached", "cached")

# validity codes (invalid kind: the record is left out and rerun once by rule)
SWEEP_OUTPUT_LEN = "SWEEP_OUTPUT_LEN"
SWEEP_PROMPT_LEN = "SWEEP_PROMPT_LEN"
SWEEP_CACHE_MODE = "SWEEP_CACHE_MODE"
SWEEP_PREPARE = "SWEEP_PREPARE"


# ── pure helpers (unit tested) ───────────────────────────────────────
def parse_task(task_id: str) -> Tuple[int, str]:
    """raf_sweep_<size>_<mode> -> (size, mode); raises ValueError otherwise."""
    parts = str(task_id or "").split("_")
    if len(parts) != 4 or parts[0] != "raf" or parts[1] != "sweep" or parts[3] not in MODES:
        raise ValueError("not a sweep task id: %r" % task_id)
    return int(parts[2]), parts[3]


def server_root(base_url: str) -> str:
    """http://host:port from a base url that may end in /v1 or /v1/chat/completions."""
    u = (base_url or "").rstrip("/")
    i = u.find("/v1")
    return u[:i] if i >= 0 else u


def build_prompt(size: int, goal_key: str, count: Callable[[str], int]) -> Tuple[str, int]:
    """Text of at most `size` tokens (by `count`, the server tokenizer), as close as possible; unique per goal key."""
    seed = int(hashlib.sha256(str(goal_key).encode()).hexdigest()[:16], 16)
    rnd = random.Random(seed)
    words = ["id%016x" % seed] + [rnd.choice(WORDS) for _ in range(size)]
    lo, hi = 1, len(words)
    while lo < hi:                                   # largest word count with tokens <= size
        mid = (lo + hi + 1) // 2
        if count(" ".join(words[:mid])) <= size:
            lo = mid
        else:
            hi = mid - 1
    text = " ".join(words[:lo])
    return text, count(text)


def check(mode: str, size: int, usage: Optional[Dict[str, Any]], prep: Optional[Dict[str, Any]]) -> List[str]:
    """Validity codes for one sweep call; empty means valid."""
    codes = []
    if not prep or not prep.get("ok"):
        codes.append(SWEEP_PREPARE)
    u = usage or {}
    if u.get("completion_tokens") != OUT_TOKENS:
        codes.append(SWEEP_OUTPUT_LEN)
    p = u.get("prompt_tokens")
    if p is None or abs(int(p) - size) > PROMPT_TOLERANCE:
        codes.append(SWEEP_PROMPT_LEN)
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    c = int(cached or 0)                             # vLLM omits the details on a miss: read as 0 for this check
    if p is not None:
        if mode == "uncached" and c > UNCACHED_MAX_CACHED:
            codes.append(SWEEP_CACHE_MODE)
        if mode == "cached" and c < int(p) - OUT_TOKENS:
            codes.append(SWEEP_CACHE_MODE)
    return codes


# ── HTTP ─────────────────────────────────────────────────────────────
def _post(url: str, body: Optional[dict]) -> Tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # localhost: never through a proxy
    with opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return int(resp.status), json.loads(raw)


def _completion(root: str, model: str, text: str, max_tokens: int) -> Dict[str, Any]:
    _, r = _post(root + "/v1/completions", {"model": model, "prompt": text, "max_tokens": max_tokens,
                                            "temperature": 0, "seed": 0, "ignore_eos": True})
    return r


# ── prepare hook (before t0) ─────────────────────────────────────────
class SweepPrepare:
    """Pre attempt hook: prompt of exactly the planned size, then reset (uncached) or warm call (cached)."""

    def __init__(self, executor: Any, base_url: str, policy: str, reset_path: Optional[str] = None,
                 decide: Any = None, metrics: Optional[Callable[[str], Any]] = None) -> None:
        self.executor, self.base_url, self.policy = executor, base_url, policy
        self.root = server_root(base_url)
        self.reset_path = reset_path or DEFAULT_RESET_PATH
        self.metrics = metrics

    def __call__(self, attempt_number: int) -> Dict[str, Any]:
        ex = self.executor
        t0 = time.time()
        rec: Dict[str, Any] = {"attempt": attempt_number, "ok": False}
        try:
            plan = dict(getattr(ex, "_raf_plan", None) or {})
            size, mode = parse_task(plan.get("task"))
            model = (getattr(ex, "config", None) or {}).get("model_id")
            key = (getattr(ex, "_raf_context", None) or {}).get("goal_key") or plan.get("task")
            count = lambda t: int(_post(self.root + "/tokenize", {"model": model, "prompt": t})[1]["count"])
            text, n = build_prompt(size, key, count)
            rec.update({"size": size, "mode": mode, "prompt_tokens_built": n, "model": model})
            if mode == "uncached":
                st, _ = _post(self.root + self.reset_path, None)
                rec["reset_status"] = st
                rec["ok"] = st == 200
            else:
                w = _completion(self.root, model, text, 1)
                wu = w.get("usage") or {}
                rec["warm_usage"] = wu
                rec["ok"] = bool(wu.get("prompt_tokens"))
            ex._sweep_prompt = {"text": text, "tokens": n, "size": size, "mode": mode, "model": model}
        except Exception as exc:                    # never raises: the goal is classified invalid afterwards
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            ex._sweep_prompt = None
        if self.metrics is not None:
            try:
                rec["cache_metrics"] = self.metrics(self.base_url)
            except Exception as exc:
                rec["cache_metrics_error"] = str(exc)
        rec["prepare_ms"] = round((time.time() - t0) * 1000.0, 1)
        store = dict(getattr(ex, "_raf_prepare", None) or {})
        store[attempt_number] = rec
        ex._raf_prepare = store
        return rec


def make_prepare(executor: Any, base_url: str, policy: str, reset_path: Optional[str] = None,
                 decide: Any = None, metrics: Optional[Callable[[str], Any]] = None) -> SweepPrepare:
    """Entry point alems.harness.prepare: sweep (same signature as the BFCL make_prepare)."""
    return SweepPrepare(executor, base_url, policy, reset_path=reset_path, decide=decide, metrics=metrics)


# ── framework (inside the window) ────────────────────────────────────
class SweepAdapter(FrameworkAdapterABC):
    """Framework adapter "raf_sweep": exactly one completion call, OUT_TOKENS output, no tools."""

    FRAMEWORK_TYPE = "raf_sweep"

    def execute_task(self, task_config: Dict[str, Any], tools: list, engine: Any) -> FrameworkResult:
        start = time.time()
        ex = task_config.get("executor")
        cfg = dict(task_config.get("model_config") or {})
        sp = getattr(ex, "_sweep_prompt", None)
        status, etype, emsg, text, usage, call_ms = "failure", None, None, "", None, 0.0
        if not sp:
            etype, emsg = "SweepPrepareMissing", "no prompt from the prepare hook"
        else:
            root = server_root(cfg.get("base_url") or cfg.get("api_endpoint") or "")
            t0 = time.time()
            try:
                r = _completion(root, sp["model"] or cfg.get("model_id"), sp["text"], OUT_TOKENS)
                call_ms = (time.time() - t0) * 1000.0
                text = ((r.get("choices") or [{}])[0].get("text")) or ""
                usage = r.get("usage") or {}
                status = "success"
            except Exception as exc:
                call_ms = (time.time() - t0) * 1000.0
                etype, emsg = type(exc).__name__, str(exc)
        wall_ms = (time.time() - start) * 1000.0
        u = usage or {}
        details = u.get("prompt_tokens_details")
        prompt, completion = u.get("prompt_tokens"), u.get("completion_tokens")
        known = details is not None and prompt is not None
        cached = int((details or {}).get("cached_tokens") or 0) if known else None
        call = {"ordinal": 1, "prompt_tokens": prompt, "completion_tokens": completion,
                "cached_tokens": cached or 0, "cached_reported": bool(details), "latency_ms": round(call_ms, 2)}
        prep = (getattr(ex, "_raf_prepare", None) or {}).get(1)
        meta = {
            "experiment_id": None, "response": text,
            "execution": {"status": status, "error_type": etype, "completed": True, "error_message": emsg,
                          "failed_steps": 0 if status == "success" else 1, "total_steps": 1},
            "tokens": {"prompt": prompt or 0, "completion": completion or 0,
                       "total": (prompt or 0) + (completion or 0)},
            "llm_calls": 1, "steps": 1, "tools_used": [], "tool_count": 0, "tool_calls": 0,
            "pending_interactions": [],
            "complexity_level": 1, "complexity_score": {},
            "orchestration_cpu_ms": 0, "total_bytes_sent": 0, "total_bytes_recv": 0,
            "total_workflow_non_local_ms": call_ms, "effective_throughput_kbps": 0,
            "phase_times": {"planning_ms": 0.0, "execution_ms": wall_ms, "synthesis_ms": 0.0},
            "phase_ratios": {"planning_ratio": 0.0, "execution_ratio": 1.0, "synthesis_ratio": 0.0},
            "api_latency_ms": call_ms, "compute_time_ms": wall_ms, "total_time_ms": wall_ms,
            "execution_time_ms": wall_ms, "avg_step_time_ms": wall_ms, "events": [],
            "raf": {"sweep": {"size": (sp or {}).get("size"), "mode": (sp or {}).get("mode"),
                              "prompt_tokens_built": (sp or {}).get("tokens"), "usage": usage,
                              "out_tokens": OUT_TOKENS},
                    "attempt": 1, "goal_key": (getattr(ex, "_raf_context", None) or {}).get("goal_key"),
                    "temperature": 0.0, "seed": 0, "llm_calls": [call], "tool_calls": []},
            "pending_state_reuse": [{
                "reuse_type": "prefix_cache", "reuse_source": "vllm",
                "tokens_reused": cached,
                "tokens_recomputed": max(0, int(prompt) - cached) if known else None,
                "reuse_fraction": (cached / prompt) if known and prompt else None,
                "cache_hit": int(cached > 0) if known else None}],
            "pending_annotations": [{"annotation_type": "raf.llm_usage", "annotation_version": "1",
                                     "source": "alems-plugin-bfcl", "payload": {"attempt": 1, "calls": [call]}}]
                                   + ([{"annotation_type": "raf.prepare", "annotation_version": "1",
                                        "source": "alems-plugin-bfcl", "payload": prep}] if prep else []),
        }
        store = dict(getattr(ex, "_raf_attempts", None) or {})
        store[1] = {"tools": [], "sweep_usage": usage, "cls": None, "fired": None}
        if ex is not None:
            ex._raf_attempts = store
        return FrameworkResult(output=text, success=status == "success", total_duration_ms=wall_ms,
                               steps=[], metadata=meta)

    def get_name(self) -> str:
        return "raf_sweep"

    def get_framework_type(self) -> str:
        return "raf_sweep"

    def is_available(self) -> bool:
        return True


# ── validity (after the goal) ────────────────────────────────────────
def classify(conn: Any, goal_id: int, plan: Dict[str, Any],
             prepare: Optional[Dict[int, Dict[str, Any]]] = None,
             attempt_meta: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Entry point alems.harness.validity: sweep. Returns {"codes": [...], "detail": {...}}."""
    try:
        size, mode = parse_task(plan.get("task"))
    except ValueError as exc:
        return {"codes": [SWEEP_PREPARE], "detail": {"error": str(exc)}}
    prep = (prepare or {}).get(1) or (prepare or {}).get("1")
    am = (attempt_meta or {}).get(1) or (attempt_meta or {}).get("1") or {}
    usage = am.get("sweep_usage")
    codes = check(mode, size, usage, prep)
    return {"codes": codes, "detail": {"size": size, "mode": mode, "usage": usage,
                                       "prepare_ok": bool(prep and prep.get("ok"))}}
