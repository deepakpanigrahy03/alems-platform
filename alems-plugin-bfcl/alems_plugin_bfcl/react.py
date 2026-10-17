"""
bfcl_react: tool calling agent for BFCL multi turn tasks (RAF LH 01, S1b).

FRAMEWORK_TYPE "bfcl_react", registered through alems.frameworks. The
harness calls execute_task() inside the measurement window, once per goal
attempt. This adapter:

1. runs an iterative tool calling loop over an OpenAI compatible endpoint
   (vLLM), one BFCL user turn after another, with an iteration limit;
2. consults the existing failure injector (passed in at runtime on the
   executor, never imported) before every LLM call and every tool call,
   with the per type ordinal of that call as step_index, and applies the
   design 5.1 behavior when a rule fires;
3. on attempt 2, recovers by full restart (FR) or shallow resume (SR)
   using the tool state snapshots of the BFCL provider (design 4.1);
4. returns the AgenticExecutor result shape in FrameworkResult.metadata so
   harness persistence is unchanged, plus a "raf" block for the pilot.

Inputs (task_config, built by harness):
    model_config  executor.config (model_id, base_url or api_endpoint, max_tokens)
    task_id       A-LEMS task id "bfcl_<bfcl id>" (or raf_context bfcl_id)
    executor      carries failure_injector, _current_attempt,
                  _current_attempt_id, and the optional _raf_context dict:
                  {goal_key, policy "FR" or "SR", temperature, seed,
                   iteration_limit, bfcl_id}

Window rules (master 5.2a): no disk, store or network I/O except requests
to the model server; tool calls are in memory. No logging inside the loop.

Imports: standard library, alems_sdk, and this package only (INV-14).
"""

import copy
import json
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.frameworks import FrameworkAdapterABC, FrameworkResult
from alems_sdk.tools import ToolExecutionContext

from .provider import BFCLToolProvider
from .tasks import load_entry

# ── design constants (design 4.1, 5.1); changing any changes the pilot ──
SYSTEM_PROMPT = ("You are an assistant that completes the user's requests "
                 "by calling the provided functions. Call functions when an "
                 "action is needed; reply in plain text when the request is done.")
ERROR_NOTICE = "[notice] The previous step failed with an error and is being retried."
MALFORMED_OUTPUT = '{"name": "unterminated", "arguments": {'
RATE_LIMIT_RETRY_AFTER_S = 2.0
TIMEOUT_BLOCK_S = 5.0
DEFAULT_ITERATION_LIMIT = 40
HTTP_TIMEOUT_S = 600

LLM_CLASSES = ("rate_limit", "json_parse")
TOOL_CLASSES = ("tool_error", "timeout")
# RAF R1: contaminating classes act on a real tool call (phase "tool"),
# persistent classes reuse clean semantics and recur once in attempt 2.
CORRUPT_CLASSES = ("silent_tool_output", "wrong_model_step")
PERSIST_PREFIX = "persistent_"
# Fixed text, identical for every option, sent when SR resumes a turn that
# failed the per turn state check (design 3: one fixed error notice).
CHECK_HASH_KEY = "actual"   # key of the public state hash in a turn_check record
DETECTED_NOTICE = ("The state after your last turn does not match the request. "
                   "Review the steps of this turn and correct the state before answering.")


from . import corrupt as _corrupt  # noqa: E402
from .env import ERROR_PREFIX as _ERROR_PREFIX  # noqa: E402
from .tasks import ground_truth as _ground_truth  # noqa: E402
from . import wrong_step as _wrong_step  # noqa: E402
from .turn_check import DetectedFailure, check_turn  # noqa: E402

# Indirection so tests can replace the clock and sleep without real waits.
_sleep = time.sleep
_now = time.time


class InjectedFailure(Exception):
    """Ends attempt 1 at the injection point (design 5.2)."""

    def __init__(self, cls: str, message: str) -> None:
        super().__init__(message)
        self.cls = cls


class IterationLimit(Exception):
    """The attempt used its LLM call budget before finishing all turns."""


class ModelError(Exception):
    """A real (not injected) model server error."""


# ── model server client ──────────────────────────────────────────────
class ChatClient:
    """Minimal OpenAI compatible /chat/completions client (no proxies)."""

    def __init__(self, base_url: str) -> None:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        self.url = base + "/chat/completions"
        # Empty ProxyHandler: localhost must never go through an HTTP proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, payload: dict) -> dict:
        """POST one request; raises ModelError on any transport or HTTP error."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=HTTP_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # urllib raises several unrelated types
            raise ModelError("%s: %s" % (type(exc).__name__, exc))


# ── per goal state, kept on the executor across attempts ─────────────
class GoalSession:
    """State of one goal execution shared by its attempts."""

    def __init__(self, goal_key: Any, bfcl_id: str) -> None:
        self.goal_key = goal_key
        self.bfcl_id = bfcl_id
        self.provider = BFCLToolProvider()
        self.provider.load_task(bfcl_id)
        self.entry = load_entry(bfcl_id)
        self.messages: List[dict] = []
        self.turn = 0
        self.initial_hash = self.provider.state_hash()
        self.failure: Optional[dict] = None   # failure point of attempt 1


def _goal_session(executor: Any, bfcl_id: str, attempt: int, ctx: dict) -> GoalSession:
    """
    Session for this goal: new on attempt 1 or when the goal key changes.

    The pilot driver sets _raf_context["goal_key"] per goal; without it the
    attempt number decides (execute_goal sets it when an injector is set).
    """
    current = getattr(executor, "_bfcl_session", None)
    key = ctx.get("goal_key")
    fresh = (attempt <= 1 or current is None or current.bfcl_id != bfcl_id
             or (key is not None and current.goal_key != key))
    if fresh:
        current = GoalSession(key, bfcl_id)
        executor._bfcl_session = current
    return current


def _turn_messages(entry: dict, turn: int) -> List[dict]:
    """User messages of one BFCL turn, as chat messages."""
    return [{"role": "user", "content": m["content"]}
            for m in entry["question"][turn] if m.get("role") == "user"]


def _tools_payload(provider: BFCLToolProvider) -> List[dict]:
    """Tool list in OpenAI function format."""
    return [{"type": "function",
             "function": {"name": d.name, "description": d.description,
                          "parameters": d.parameters}}
            for d in provider.get_tools()]


def _bfcl_id(task_config: dict, ctx: dict) -> str:
    """BFCL entry id from raf_context or the A-LEMS task id."""
    if ctx.get("bfcl_id"):
        return ctx["bfcl_id"]
    task_id = str(task_config.get("task_id") or "")
    return task_id[len("bfcl_"):] if task_id.startswith("bfcl_") else task_id


# ── one attempt ──────────────────────────────────────────────────────
class AttemptRun:
    """Runs one attempt of a goal and builds its result."""

    def __init__(self, session: GoalSession, executor: Any, client: Any,
                 model_config: dict, ctx: dict, attempt: int) -> None:
        self.s = session
        self.executor = executor
        self.client = client
        self.cfg = model_config
        self.ctx = ctx
        self.attempt = attempt
        prior = getattr(session, "failure", None) or {}
        # Persistent: the fault is still there after one recovery, so the
        # injector stays active for attempt 2 only (attempt 3 runs clean).
        self.persist = (attempt == 2 and str(prior.get("injected_cls") or "")
                        .startswith(PERSIST_PREFIX))
        self.persist_fired = False
        self.injector = (getattr(executor, "failure_injector", None)
                         if attempt == 1 or self.persist else None)
        self.check_turns = bool(ctx.get("turn_check"))
        self.turn_checks: List[dict] = []
        # Strings the agent has seen in this attempt (arguments and results);
        # the plausible pool for string corruption.
        self.seen_strings: List[str] = []
        self.limit = int(ctx.get("iteration_limit", DEFAULT_ITERATION_LIMIT))
        self.tools = _tools_payload(session.provider)
        self.llm_ordinal = 0
        self.tool_ordinal = 0
        self.draws = 0
        self.interactions: List[dict] = []
        self.events: List[dict] = []
        self.calls: List[dict] = []         # per LLM call: tokens, cached
        self.calls_tools: List[dict] = []   # per executed tool call
        self.checkpoints: List[dict] = []   # one per turn end (raf.context_checkpoint)
        self.notice_text = ERROR_NOTICE      # SG replaces it with the state facts
        self.injection: Dict[str, Any] = {"scheduled_checks": 0, "fired": 0}
        self.recovery: Dict[str, Any] = {}

    # ── injection ────────────────────────────────────────────────────
    def _check(self, phase: str, ordinal: int, tool_name: Optional[str]) -> Optional[str]:
        """Ask the injector; returns the failure class that fired, or None."""
        inj = self.injector
        if inj is None or not getattr(inj, "is_active", lambda: True)():
            return None
        if self.attempt >= 2:
            # Persistent recurrence point, identical under every depth:
            # llm class at the first model call of attempt 2, tool class at
            # the first call of the same tool. The injector sees the attempt
            # 1 ordinal so the planned rule matches and is logged as usual.
            ordinal = self._persist_ordinal(phase, tool_name)
            if ordinal is None:
                return None
        self.draws += 1
        self.injection["scheduled_checks"] += 1
        fire, cls = inj.should_inject(
            step_index=ordinal, phase=phase, tool_name=tool_name,
            draw_number=self.draws,
            attempt_id=getattr(self.executor, "_current_attempt_id", 0) or 0,
            goal_id=getattr(self.executor, "_current_goal_id", 0) or 0)
        if not fire:
            return None
        base = cls[len(PERSIST_PREFIX):] if cls.startswith(PERSIST_PREFIX) else cls
        allowed = LLM_CLASSES if phase == "llm" else TOOL_CLASSES + CORRUPT_CLASSES
        if self.attempt >= 2:
            self.persist_fired = True
        self.injection.update(fired=1, cls=cls, call_type=phase, ordinal=ordinal,
                              tool_name=tool_name, applied=base in allowed)
        return base if base in allowed else None

    def _persist_ordinal(self, phase: str, tool_name: Optional[str]) -> Optional[int]:
        """Attempt 1 ordinal to present to the injector, or None (no trigger)."""
        f = getattr(self.s, "failure", None) or {}
        if not self.persist or self.persist_fired or f.get("kind") != phase:
            return None
        if phase == "tool":
            if tool_name != f.get("tool_name"):
                return None
            return f.get("tool_ordinal")
        return f.get("llm_ordinal")

    def _record_failure(self, cls: str, kind: str, extra: dict) -> None:
        """Remember the failure point for attempt 2 (design 4.1)."""
        f = {"cls": cls, "kind": kind, "turn": self.s.turn,
             "llm_ordinal": self.llm_ordinal, "tool_ordinal": self.tool_ordinal,
             "provider_calls": self.s.provider.env.calls_made,
             "state_hash": self.s.provider.state_hash(),
             "turn_starts": list(self.s.provider.turn_starts),
             "msg_starts": list(getattr(self.s, "msg_starts", None) or []),
             "injected_cls": self.injection.get("cls"),
             "tool_name": self.injection.get("tool_name")}
        f.update(extra)
        self.s.failure = f
        self.injection["failure_point"] = {k: v for k, v in f.items()
                                           if k not in ("messages", "assistant")}

    # ── events and interactions (AgenticExecutor shapes) ─────────────
    def _event(self, event_type: str, t0: float, t1: float, meta: dict) -> None:
        self.events.append({
            "phase": "execution", "event_type": event_type,
            "start_time_ns": int(t0 * 1e9), "end_time_ns": int(t1 * 1e9),
            "duration_ns": int((t1 - t0) * 1e9), "metadata": meta,
            "attempt_id": getattr(self.executor, "_current_attempt_id", None)})

    def _interaction(self, messages: List[dict], content: str, usage: dict,
                     t0: float, t1: float, status: str, error: str = None) -> dict:
        row = {
            "step_index": self.llm_ordinal, "workflow_type": "agentic",
            "prompt": json.dumps(messages, separators=(",", ":")),
            "response": content, "model_name": self.cfg.get("model_id", "unknown"),
            "provider": self.cfg.get("provider", "vllm_local"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "total_time_ms": (t1 - t0) * 1000.0, "preprocess_ms": 0.0,
            "non_local_ms": (t1 - t0) * 1000.0, "local_compute_ms": 0.0,
            "postprocess_ms": 0.0, "app_throughput_kbps": 0.0,
            "bytes_sent_approx": len(json.dumps(messages)), "bytes_recv_approx": len(content or ""),
            "tcp_retransmits": None, "cpu_percent_during_wait": None,
            "ttft_ms": None, "tpot_ms": None, "token_throughput": None,
            "streaming_enabled": 0, "first_token_time_ns": None,
            "last_token_time_ns": None, "request_start_ns": int(t0 * 1e9),
            "status": status}
        if error:
            row["error_message"] = error
        self.interactions.append(row)
        return row

    # ── LLM call ─────────────────────────────────────────────────────
    def _llm(self) -> dict:
        """One LLM call with injection; returns the assistant message."""
        if self.llm_ordinal >= self.limit:
            raise IterationLimit("iteration limit %d reached" % self.limit)
        self.llm_ordinal += 1
        cls = self._check("llm", self.llm_ordinal, None)
        messages = self.s.messages
        if cls == "rate_limit":
            # Rejected before generation: nothing is sent to the server.
            t = _now()
            self._record_failure(cls, "llm", {"messages": copy.deepcopy(messages)})
            self._interaction(messages, "", {}, t, t, "failure", "injected rate_limit 429")
            raise InjectedFailure(cls, "rate limit 429, retry after %.0f s" % RATE_LIMIT_RETRY_AFTER_S)
        payload = {"model": self.cfg.get("model_id"), "messages": messages,
                   "tools": self.tools, "temperature": float(self.ctx.get("temperature", 0.0)),
                   "seed": int(self.ctx.get("seed", 0)),
                   "max_tokens": int(self.cfg.get("max_tokens", 1024))}
        t0 = _now()
        resp = self.client.complete(payload)
        t1 = _now()
        msg = resp["choices"][0]["message"]
        usage = resp.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        self.calls.append({"ordinal": self.llm_ordinal,
                           "prompt_tokens": usage.get("prompt_tokens"),
                           "completion_tokens": usage.get("completion_tokens"),
                           "cached_tokens": details.get("cached_tokens") or 0,
                           "cached_reported": bool(details)})
        content = msg.get("content") or ""
        if cls == "json_parse":
            # Full generation happened; the agent sees malformed output.
            self._record_failure(cls, "llm", {"messages": copy.deepcopy(messages)})
            self._interaction(messages, MALFORMED_OUTPUT, usage, t0, t1, "failure",
                              "injected json_parse")
            self._event("llm_call", t0, t1, {"ordinal": self.llm_ordinal, "injected": cls})
            raise InjectedFailure(cls, "JSON parse failed on model output")
        self._interaction(messages, content or json.dumps(msg.get("tool_calls")), usage,
                          t0, t1, "success")
        self._event("llm_call", t0, t1, {"ordinal": self.llm_ordinal})
        return msg

    # ── tool call ────────────────────────────────────────────────────
    def _tool(self, call: dict, notice: bool = False) -> dict:
        """Execute one tool call with injection; returns the tool message."""
        fn = call.get("function", {})
        name = fn.get("name", "")
        self.tool_ordinal += 1
        cls = self._check("tool", self.tool_ordinal, name)
        t0 = _now()
        if cls in TOOL_CLASSES:
            if cls == "timeout":
                _sleep(TIMEOUT_BLOCK_S)
            t1 = _now()
            self._event("tool_call", t0, t1, {"tool": name, "ordinal": self.tool_ordinal,
                                               "injected": cls})
            raise InjectedFailure(cls, "%s on tool %s" % (cls, name))
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments are not an object")
        except ValueError:
            # Keep ordinals aligned with provider calls: an unusable argument
            # string still goes through the provider and fails there.
            args = {"__invalid_arguments__": fn.get("arguments")}
        if cls == "wrong_model_step":
            args = self._corrupt_args(call, args)
        res = self.s.provider.execute(name, args, ToolExecutionContext(db_path=""))
        t1 = _now()
        self.calls_tools.append({"ordinal": self.tool_ordinal, "name": name,
                                 "success": res.success})
        self._event("tool_call", t0, t1, {"tool": name, "ordinal": self.tool_ordinal,
                                           "success": res.success})
        text = str(res.result)
        if cls == "silent_tool_output":
            text = self._corrupt_text(text)
        self._remember(args, text)
        if notice:
            text = text + "\n" + self.notice_text
        return {"role": "tool", "tool_call_id": call.get("id", ""), "name": name,
                "content": text}

    # ── contamination (RAF R1) ───────────────────────────────────────
    def _seed(self, cls: str) -> int:
        key = getattr(self.s, "goal_key", None) or self.s.entry["id"]
        return _corrupt.seed_of(key, cls, self.tool_ordinal)

    def _corrupt_args(self, call: dict, args: dict) -> dict:
        """
        wrong_model_step: corrupt one argument and rewrite the assistant's
        tool call in history, so the context shows the model's own mistake.
        """
        # E2b: only a corruption the tool accepts and that changes state differently.
        name = (call.get("function") or {}).get("name", "")
        seen = list(self.seen_strings) + list(getattr(self, "seen_numbers", []))
        new, detail = _wrong_step.choose(self.s.provider.env, name, args,
                                         self._seed("wrong_model_step"), seen)
        self.injection["corruption"] = detail
        if new is None:
            self.injection["applied"] = False
            return args
        call.setdefault("function", {})["arguments"] = json.dumps(new)
        return new

    def _corrupt_text(self, text: str) -> str:
        """silent_tool_output: the real result with one leaf changed."""
        new, detail = _corrupt.corrupt_result(text, self._seed("silent_tool_output"),
                                              _ERROR_PREFIX, self.seen_strings)
        self.injection["corruption"] = detail
        if new is None:
            self.injection["applied"] = False
            return text
        return new

    def _remember(self, args: dict, text: str) -> None:
        """Add the strings of one executed call to the seen pool (as the agent saw them)."""
        self.seen_strings.extend(_corrupt.strings_of(args))
        if not hasattr(self, "seen_numbers"):
            self.seen_numbers = []           # ints and floats seen in this attempt
        self.seen_numbers.extend(v for _, v in _corrupt.leaves(args)
                                 if isinstance(v, (int, float)) and not isinstance(v, bool))
        try:
            parsed = json.loads(text)
            self.seen_strings.extend(_corrupt.strings_of(parsed))
            self.seen_numbers.extend(v for _, v in _corrupt.leaves(parsed)
                                     if isinstance(v, (int, float)) and not isinstance(v, bool))
        except ValueError:
            pass  # plain text results add nothing

    def _turn_end(self, t: int) -> None:
        """Per turn state check (spec 4.3); raises DetectedFailure on mismatch."""
        # Context checkpoint (coordinator Oct 15): in memory only; state_hash
        # is filled from the turn check below when checks run, else NULL.
        self.checkpoints.append({
            "turn": t, "message_index": len(self.s.messages),
            "prompt_token_offset": (self.calls[-1].get("prompt_tokens")
                                    if self.calls else None),
            "state_hash": None})
        if not self.check_turns:
            return
        start = self.s.provider.turn_starts[t]
        gt = _ground_truth(self.s.entry["id"])[t]
        rec = check_turn(self.s.provider.env, start, gt)
        rec.update(attempt=self.attempt, turn=t)
        self.turn_checks.append(rec)
        self.checkpoints[-1]["state_hash"] = rec.get(CHECK_HASH_KEY)
        self.injection["turn_checks"] = self.turn_checks
        if rec["ok"]:
            return
        self._record_failure("silent_detected", "turn_check",
                             {"messages": copy.deepcopy(self.s.messages)})
        raise DetectedFailure(t, rec)

    # ── loop ─────────────────────────────────────────────────────────
    def _run_tool_calls(self, assistant: dict, start: int, notice_first: bool) -> None:
        """Run the tool calls of one assistant message from index start."""
        for j, call in enumerate(assistant.get("tool_calls") or []):
            if j < start:
                continue
            before = copy.deepcopy(self.s.messages)
            try:
                self.s.messages.append(self._tool(call, notice_first and j == start))
            except InjectedFailure as exc:
                self._record_failure(exc.cls, "tool",
                                     {"messages": before, "call_index": j})
                raise

    def _finish_turn(self) -> None:
        """Call the model until it answers without tool calls."""
        while True:
            msg = self._llm()
            calls = msg.get("tool_calls") or []
            assistant = {"role": "assistant", "content": msg.get("content")}
            if calls:
                assistant["tool_calls"] = calls
            self.s.messages.append(assistant)
            if not calls:
                return
            self._run_tool_calls(assistant, 0, False)

    def _run_turns(self, first_turn: int) -> None:
        """Open and run every turn from first_turn to the last one."""
        for t in range(first_turn, len(self.s.entry["question"])):
            self.s.turn = t
            self.s.provider.begin_turn(t)
            # Message index of the turn start; RB cuts the history here.
            starts = getattr(self.s, "msg_starts", None) or []
            self.s.msg_starts = starts[:t] + [len(self.s.messages)]
            self.s.messages.extend(_turn_messages(self.s.entry, t))
            notice = getattr(self, "rb_notice", None)
            if notice is not None and t == first_turn:
                self.s.messages.append({"role": "user", "content": notice})
                self.rb_notice = None
            self._finish_turn()
            self._turn_end(t)

    # ── recovery (attempt 2) ─────────────────────────────────────────
    def _recover(self) -> Tuple[int, bool, Optional[dict]]:
        """
        Apply the recovery policy before the loop resumes.

        Returns:
            (first turn, whether to open it, SR tool resume info or None)
        """
        f = self.s.failure
        policy = str(self.ctx.get("policy", "FR")).upper()
        # Only a missing failure point forces FR. Attempt 3 policy is owned by
        # PreAttempt (fixed arm keeps it, escalating arm switches it); forcing
        # FR here gave SR state with an FR context (R2 finding).
        if f is None:
            policy = "FR"     # no recorded failure point: nothing to resume
        if f is not None and f["cls"] == "rate_limit":
            t0 = _now()
            _sleep(RATE_LIMIT_RETRY_AFTER_S)
            self._event("waiting", t0, _now(), {"reason": "retry_after"})
        # S4: restore normally happens in prepare, outside the window; in that
        # case only the in memory history is rebuilt here (no copy, no hash).
        prepared = getattr(self.s, "prepared_restore", None)
        self.s.prepared_restore = None
        t0 = time.perf_counter_ns()
        before = (prepared["state_hash_before"] if prepared
                  else self.s.provider.state_hash())
        self.recovery = {"policy": policy, "state_hash_before_restore": before,
                         "restored_in_prepare": prepared is not None}
        if policy == "FR":
            if prepared is None:
                self.s.provider.restore_initial()
            self.s.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            resume = (0, True, None)
            self.recovery["expected_hash"] = self.s.initial_hash
        elif policy == "RB":
            resume = self._restore_rollback(f, prepared is not None)
            self.recovery["expected_hash"] = None   # checked by call count
        else:
            if policy == "SG":
                # Facts built in prepare, outside the window (Addendum C).
                self.notice_text = getattr(self.s, "sg_notice", None) or ERROR_NOTICE
                self.recovery["sg_notice_chars"] = len(self.notice_text)
            resume = self._restore_shallow(f, prepared is not None)
            self.recovery["expected_hash"] = f["state_hash"]
        if prepared:
            self.recovery["state_hash_after_restore"] = prepared["state_hash_after"]
            self.recovery["restore_ns"] = 0
            self.recovery["prepare_restore_ns"] = prepared["ns"]
        else:
            self.recovery["state_hash_after_restore"] = self.s.provider.state_hash()
            self.recovery["restore_ns"] = time.perf_counter_ns() - t0
        self.recovery["notice"] = policy in ("SR", "RB", "SG")
        return resume

    def _restore_rollback(self, f: dict, restored: bool = False) -> Tuple[int, bool, Optional[dict]]:
        """RB: history and tool state back to the start of the suspect turn."""
        prov = self.s.provider
        t = int(f["turn"])
        start = int(f["turn_starts"][t])
        if not restored:
            if prov.env.calls_made > start:
                prov.restore_before(start + 1)
            prov.turn_starts = list(f["turn_starts"][:t])
        cut = int(f["msg_starts"][t])
        self.s.messages = copy.deepcopy(f["messages"][:cut])
        self.s.msg_starts = list(f["msg_starts"][:t])
        self.s.turn = t
        # One fixed notice after the reopened turn's user message (spec 2.1).
        self.rb_notice = (DETECTED_NOTICE if f["kind"] == "turn_check"
                          else ERROR_NOTICE)
        return t, True, None

    def _restore_shallow(self, f: dict, restored: bool = False) -> Tuple[int, bool, Optional[dict]]:
        """SR: keep calls before the failed one, restore the snapshot before it."""
        prov = self.s.provider
        k = f["provider_calls"] + 1          # ordinal of the failed tool call
        if not restored and prov.env.calls_made >= k:
            prov.restore_before(k)
        prov.turn_starts = list(f["turn_starts"])
        self.s.messages = copy.deepcopy(f["messages"])
        self.s.turn = f["turn"]

        if f["kind"] == "turn_check":
            # SR after detection keeps the whole, poisoned context (nothing is
            # restored: k exceeds calls_made) and the agent redoes turn t.
            self.s.messages.append({"role": "user", "content": DETECTED_NOTICE})
            return f["turn"], False, None
        if f["kind"] == "llm":
            # Rerun LLM call k with the notice at the end of the history.
            last = self.s.messages[-1]
            last["content"] = (last.get("content") or "") + "\n" + self.notice_text
            return f["turn"], False, None
        # Rerun tool call k (and the later calls of the same assistant message).
        return f["turn"], False, {"call_index": f["call_index"]}

    # ── entry ────────────────────────────────────────────────────────
    def run(self) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Execute the attempt.

        Returns:
            (status, error_type, error_message), status success or failure.
        """
        try:
            if self.attempt == 1:
                self.s.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                self._run_turns(0)
            else:
                first, open_first, tool_resume = self._recover()
                if tool_resume is not None:
                    self._resume_tool_call(tool_resume["call_index"])
                if not open_first:
                    # SR: finish the interrupted turn, then the remaining ones.
                    self._finish_turn()
                    self._turn_end(self.s.turn)
                    first = self.s.turn + 1
                self._run_turns(first)
            return "success", None, None
        except DetectedFailure as exc:
            return "failure", exc.cls, str(exc)
        except InjectedFailure as exc:
            return "failure", exc.cls, str(exc)
        except IterationLimit as exc:
            return "failure", "iteration_limit", str(exc)
        except ModelError as exc:
            return "failure", "api_error", str(exc)

    def _resume_tool_call(self, call_index: int) -> None:
        """SR after a tool failure: rerun the failed call and its successors."""
        assistant = self.s.messages[-1 - self._tool_results_after_last_assistant()]
        self._run_tool_calls(assistant, call_index, True)

    def _tool_results_after_last_assistant(self) -> int:
        """Number of tool messages at the end of the history."""
        n = 0
        for m in reversed(self.s.messages):
            if m.get("role") != "tool":
                break
            n += 1
        return n


# ── result assembly ──────────────────────────────────────────────────
def _metadata(run: AttemptRun, status: str, etype: Optional[str], emsg: Optional[str],
              response: str, wall_ms: float) -> dict:
    """AgenticExecutor result shape plus the raf block."""
    prompt = sum(c.get("prompt_tokens") or 0 for c in run.calls)
    completion = sum(c.get("completion_tokens") or 0 for c in run.calls)
    tools_used = sorted({c["name"] for c in run.calls_tools})
    llm_ms = sum(i["total_time_ms"] for i in run.interactions)
    s = run.s
    return {
        "experiment_id": None, "response": response,
        "execution": {"status": status, "error_type": etype, "completed": True,
                      "error_message": emsg, "failed_steps": 0 if status == "success" else 1,
                      "total_steps": run.llm_ordinal + run.tool_ordinal},
        "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
        "llm_calls": run.llm_ordinal, "steps": run.llm_ordinal,
        "tools_used": tools_used, "tool_count": run.tool_ordinal,
        "tool_calls": run.tool_ordinal,
        "pending_interactions": run.interactions,
        "complexity_level": 3, "complexity_score": {},
        "orchestration_cpu_ms": 0, "total_bytes_sent": 0, "total_bytes_recv": 0,
        "total_workflow_non_local_ms": llm_ms, "effective_throughput_kbps": 0,
        "phase_times": {"planning_ms": 0.0, "execution_ms": wall_ms, "synthesis_ms": 0.0},
        "phase_ratios": {"planning_ratio": 0.0, "execution_ratio": 1.0, "synthesis_ratio": 0.0},
        "api_latency_ms": llm_ms, "compute_time_ms": wall_ms, "total_time_ms": wall_ms,
        "execution_time_ms": wall_ms,
        "avg_step_time_ms": wall_ms / max(1, run.llm_ordinal + run.tool_ordinal),
        "events": run.events,
        "raf": {
            "bfcl_id": s.bfcl_id, "attempt": run.attempt, "goal_key": s.goal_key,
            "iteration_limit": run.limit,
            "temperature": float(run.ctx.get("temperature", 0.0)),
            "seed": int(run.ctx.get("seed", 0)),
            "llm_ordinals": run.llm_ordinal, "tool_ordinals": run.tool_ordinal,
            "llm_calls": run.calls, "tool_calls": run.calls_tools,
            "injection": run.injection, "recovery": run.recovery,
            "initial_state_hash": s.initial_hash,
            "final_state_hash": s.provider.state_hash(),
            "scorer_output": s.provider.scorer_output(),
        },
    }


def _state_reuse_rows(calls: list) -> list:
    """
    One state_reuse_events row per LLM call: prefix cache tokens the serving
    engine reported as reused. execute_goal persists them after insert_run
    (run_id and attempt_id known), never inside the measurement window.

    Returns:
        list of dicts with the state_reuse_events columns except the ids;
        unreported usage gives NULL fields, never 0 (MIC-1).
    """
    rows = []
    for c in calls:
        prompt = c.get("prompt_tokens")
        known = bool(c.get("cached_reported")) and prompt is not None
        cached = int(c.get("cached_tokens") or 0) if known else None
        rows.append({"reuse_type": "prefix_cache", "reuse_source": "vllm",
                     "tokens_reused": cached,
                     "tokens_recomputed": max(0, int(prompt) - cached) if known else None,
                     "reuse_fraction": (cached / prompt) if known and prompt else None,
                     "cache_hit": int(cached > 0) if known else None})
    return rows


def _annotations(attempt: int, calls: list, prep: Optional[dict]) -> list:
    """
    Span annotations for the attempt span (D8.5b), written by execute_goal
    after t1. raf.llm_usage carries request level usage per LLM call (cached
    tokens have no column in llm_interactions); raf.prepare carries the
    resets, restore and validity codes of this attempt's prepare step.
    """
    out = [{"annotation_type": "raf.llm_usage", "annotation_version": "1",
            "source": "alems-plugin-bfcl",
            "payload": {"attempt": attempt, "calls": calls}}]
    if prep is not None:
        out.append({"annotation_type": "raf.prepare", "annotation_version": "1",
                    "source": "alems-plugin-bfcl", "payload": prep})
    return out


class BFCLReactAdapter(FrameworkAdapterABC):
    """Framework adapter "bfcl_react"; see module docstring."""

    FRAMEWORK_TYPE = "bfcl_react"

    def __init__(self) -> None:
        super().__init__()
        # Tests replace this with a scripted client; production uses vLLM.
        self.client_factory = ChatClient

    def execute_task(self, task_config: Dict[str, Any], tools: list,
                     engine: Any) -> FrameworkResult:
        """Run one attempt; never raises for model or tool failures."""
        start = _now()
        executor = task_config.get("executor")
        cfg = dict(task_config.get("model_config") or {})
        ctx = dict(getattr(executor, "_raf_context", None) or {})
        attempt = int(getattr(executor, "_current_attempt", 1) or 1)
        if getattr(executor, "failure_injector", None) is None:
            attempt = 1 if ctx.get("goal_key") is None else attempt
        session = _goal_session(executor, _bfcl_id(task_config, ctx), attempt, ctx)
        client = self.client_factory(cfg.get("base_url") or cfg.get("api_endpoint") or "")
        run = AttemptRun(session, executor, client, cfg, ctx, attempt)
        status, etype, emsg = run.run()
        last = next((m for m in reversed(session.messages)
                     if m.get("role") == "assistant" and m.get("content")), {})
        response = last.get("content") or ""
        wall_ms = (_now() - start) * 1000.0
        # S4: the scored output is the canonical call list JSON (already a
        # string) the bfcl_state scorer reads; the assistant text stays in
        # FrameworkResult.output and in llm_interactions.
        scored = session.provider.scorer_output()
        meta = _metadata(run, status, etype, emsg, scored, wall_ms)
        prep = (getattr(executor, "_raf_prepare", None) or {}).get(attempt)
        meta["raf"]["prepare"] = prep
        meta["pending_annotations"] = _annotations(attempt, run.calls, prep)
        meta["pending_state_reuse"] = _state_reuse_rows(run.calls)
        meta["pending_annotations"].append(
            {"annotation_type": "raf.context_checkpoint", "annotation_version": "1",
             "source": "alems-plugin-bfcl",
             "payload": {"attempt": attempt, "checkpoints": run.checkpoints}})
        store = dict(getattr(executor, "_raf_attempts", None) or {})
        store[attempt] = {k: run.injection.get(k)
                          for k in ("cls", "fired", "applied", "corruption", "turn_checks")}
        # B5: tool name sequence per attempt (trajectory divergence under cache state).
        store[attempt]["tools"] = [c["name"] for c in run.calls_tools]
        executor._raf_attempts = store
        if run.turn_checks:
            # Per turn check cost and outcome (spec 4.3); hashes kept for audit.
            meta["pending_annotations"].append(
                {"annotation_type": "raf.turn_check", "annotation_version": "1",
                 "source": "alems-plugin-bfcl",
                 "payload": {"attempt": attempt, "checks": run.turn_checks,
                             "total_ms": sum(c["ms"] for c in run.turn_checks)}})
        return FrameworkResult(output=response, success=status == "success",
                               total_duration_ms=wall_ms, steps=[], metadata=meta)

    def get_name(self) -> str:
        return "bfcl_react"

    def get_framework_type(self) -> str:
        return "bfcl_react"

    def is_available(self) -> bool:
        return True
