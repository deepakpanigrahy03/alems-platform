"""
core/vocabularies/agent/span_builder.py -- spans of one run from its result dict.

Called after the measurement window, when the run row exists (EEI-4). Every span
is RECORDED with times from its source (SpanWriter.record_span), never from a clock
read during building (45.8):

    run        ml_features start_time_ns (tpre) .. end_time_ns (t2); events task_start (t0),
               task_end (t1) from pre_task_duration_ns and task_duration_ns
    phase,     orchestration event start_time_ns .. end_time_ns (else start + duration_ns,
    tool_call  derived)
    llm_call   request_start_ns .. last_token_time_ns (the end of the response stream,
               observed; the non stream path sets it to the response end); else start +
               api_latency_ms (derived); else unknown
Clock: wall clock epoch ns, the clock of runs and energy samples. Monotonic times are
not in these sources and stay NULL.

Structure (45.8): goal (root) -> attempt -> run -> phase -> tool_call, llm_call.
record_goal_tree writes goal, attempt and run for a single attempt goal (save_pair,
save_single); execute_goal writes its own goal and attempts. build_spans_from_result
writes only the run's children.

Identity and parent (45.8 D): an event or interaction captured live carries span_id and
parent_span_id (core.observability.live_spans); the span is recorded with that id, and
its parent is 'observed' (the phase running when the call started, or the run when none
was). Only data without live ids (older results, the historical repair) falls back to the
phase whose interval contains the call ('inferred'), else the run ('default').

Rule S: additive only. Never raises -- errors logged and skipped.
"""

import logging
import socket
import uuid

logger = logging.getLogger(__name__)

_CPU_DEVICE = "cpu_package"
_GPU_DEVICE = "gpu_0"


def run_interval(result):
    # type: (dict) -> tuple
    """(tpre, t2, t0, t1, quality) of the run from its result; unknown parts are None."""
    ml = result.get("ml_features") or {}
    tpre = ml.get("start_time_ns") or result.get("start_time_ns")
    t2 = ml.get("end_time_ns") or result.get("end_time_ns")
    quality = "observed"
    if t2 is None and tpre is not None and ml.get("duration_ns"):
        t2, quality = tpre + ml["duration_ns"], "derived"
    t0 = tpre + ml["pre_task_duration_ns"] if tpre is not None and ml.get("pre_task_duration_ns") is not None else None
    t1 = t0 + ml["task_duration_ns"] if t0 is not None and ml.get("task_duration_ns") is not None else None
    return tpre, t2, t0, t1, quality if t2 is not None else "unknown"


def record_run_span(writer, name, result, parent_span_id=None, span_id=None, parent_source="observed"):
    # type: (...) -> str
    """Run span over [tpre, t2] with task_start (t0) and task_end (t1) events; returns its id."""
    tpre, t2, t0, t1, quality = run_interval(result)
    sid = writer.record_span("run", name, parent_span_id, tpre, t2, timing_source="runs",
                             timing_quality=quality, parent_source=parent_source, span_id=span_id)
    if sid is None:   # no known start: keep a placeholder id so children still group
        logger.warning("span_builder: run %s has no start time; spans of this run not recorded", name)
        return None
    if t0 is not None:
        writer.add_event(sid, "task_start", t0)
    if t1 is not None:
        writer.add_event(sid, "task_end", t1)
    return sid


def record_goal_tree(writer, workflow_type, task_id, result, outcome):
    # type: (...) -> dict
    """
    Goal -> attempt 1 -> run for a goal with exactly one attempt whose window is the run's.
    Span ids are allocated here so the caller can insert the goal and attempt rows with
    them (no later backfill). Returns {"goal", "attempt", "run"} (run None if no start).
    """
    tpre, t2, _, _, quality = run_interval(result)
    ml = result.get("ml_features") or {}
    ids = {"goal": uuid.uuid4().hex, "attempt": uuid.uuid4().hex}
    if writer.record_span("goal", "{}:{}".format(workflow_type, task_id), None, tpre, t2,
                          timing_source="runs", timing_quality=quality, span_id=ids["goal"],
                          attributes={"workflow_type": workflow_type, "task_id": task_id,
                                      "attributed_energy_uj": ml.get("attributed_energy_uj"),
                                      "outcome": outcome}) is None:
        return {"goal": None, "attempt": None, "run": None}
    writer.record_span("attempt", "attempt:1", ids["goal"], tpre, t2, timing_source="runs",
                       timing_quality=quality, span_id=ids["attempt"],
                       attributes={"attempt_number": 1, "is_retry": False, "outcome": outcome})
    ids["run"] = record_run_span(writer, "{}:{}".format(workflow_type, task_id), result,
                                 parent_span_id=ids["attempt"])
    return ids


def build_spans_from_result(writer, run_span_id, result, workflow_type, hw_info):
    # type: (...) -> None
    """Children of one run: phases, tool calls, LLM calls (goal and attempt: callers)."""
    if run_span_id is None:
        return
    try:
        _build(writer, run_span_id, result, workflow_type, hw_info)
    except Exception as exc:
        logger.warning("span_builder: workflow=%s skipped: %s", workflow_type, exc)
        try:
            from core.observability import errors as _obs_errors
            _obs_errors.capture(exc, code="ALEMS-SPAN-0001", component=__name__)
        except Exception as _ce:  # noqa: BLE001  capture never breaks span building (5.2a)
            logger.debug("error capture unavailable: %s", _ce)


def _build(writer, run_span_id, result, workflow_type, hw_info):
    # type: (...) -> None
    node = socket.gethostname().lower()
    ml = result.get("ml_features") or {}
    has_gpu = bool(ml.get("gpu_total_energy_uj") or ml.get("gpu_dynamic_energy_uj"))
    events = result.get("orchestration_events") or []
    phases = []   # (start, end, span_id) of recorded phase spans with both ends
    for ev in events:
        if (ev.get("event_type") or "") != "tool_call":
            sid = _build_orch_span(writer, run_span_id, ev, node, has_gpu)
            rec = writer.get(sid) if sid else None
            if rec is not None and rec.end_ns is not None:
                phases.append((rec.start_ns, rec.end_ns, sid))
    for ev in events:
        if (ev.get("event_type") or "") == "tool_call":
            parent, how = _parent(writer, ev, phases, ev.get("start_time_ns"), _orch_end(ev)[0], run_span_id)
            _build_orch_span(writer, parent, ev, node, has_gpu, parent_source=how)
    for ix in result.get("pending_interactions") or []:
        parent, how = _parent(writer, ix, phases, ix.get("request_start_ns"), _llm_end(ix)[0], run_span_id)
        _build_llm_span(writer, parent, ix, node, has_gpu, parent_source=how)
    for fev in result.get("span_failed_events") or []:
        writer.add_event(run_span_id, fev["event_type"], fev.get("ts_ns"), fev.get("attributes", {}))


def _parent(writer, item, phases, start, end, run_span_id):
    # type: (...) -> tuple
    """Live parent when captured (observed), else the containing phase (inferred) or the run."""
    if item.get("span_id"):
        live_parent = item.get("parent_span_id")
        if live_parent and writer.get(live_parent) is not None:
            return live_parent, "observed"
        if not live_parent:
            return run_span_id, "observed"     # started while no phase was running
    return _container(phases, start, end, run_span_id)


def _container(phases, start, end, default):
    # type: (list, object, object, str) -> tuple
    """(parent id, parent_source): the unique innermost phase containing [start, end], else default."""
    if start is None or end is None:
        return default, "default"
    inside = [p for p in phases if p[0] <= start and end <= p[1]]
    if not inside:
        return default, "default"
    inside.sort(key=lambda p: p[1] - p[0])
    if len(inside) > 1 and inside[0][1] - inside[0][0] == inside[1][1] - inside[1][0]:
        return default, "default"   # two equally tight candidates: not unambiguous
    return inside[0][2], "inferred"


def _orch_end(ev):
    # type: (dict) -> tuple
    start, end = ev.get("start_time_ns"), ev.get("end_time_ns")
    if end is not None:
        return end, "observed"
    if start is not None and ev.get("duration_ns") is not None:
        return start + ev["duration_ns"], "derived"
    return None, "unknown"


def _build_orch_span(writer, parent_id, ev, node, has_gpu, parent_source="observed"):
    # type: (...) -> object
    phase = ev.get("phase") or "unknown"
    ev_type = ev.get("event_type") or "unknown"
    start_ns = ev.get("start_time_ns")
    if not start_ns:
        return None
    end_ns, quality = _orch_end(ev)
    meta = ev.get("metadata") or {}
    tool_success = ev.get("tool_success") if "tool_success" in ev else meta.get("success", True)
    is_tool = ev_type == "tool_call"
    attrs = {"phase": phase, "event_energy_uj": ev.get("event_energy_uj"), "duration_ns": ev.get("duration_ns")}
    if is_tool:
        attrs.update(tool_name=ev.get("tool_name"), tool_success=tool_success)
    span_id = writer.record_span(
        "tool_call" if is_tool else "phase",
        "tool:{}".format(ev.get("tool_name") or meta.get("tool") or "unknown") if is_tool else "phase:{}".format(phase),
        parent_id, start_ns, end_ns, timing_source="orchestration_events", timing_quality=quality,
        span_id=ev.get("span_id"),
        parent_source=parent_source, status="error" if is_tool and not tool_success else "closed",
        attributes=attrs)
    if span_id is None:
        return None
    writer.add_placement(span_id, node=node, device=_CPU_DEVICE, phase=phase)
    if has_gpu and phase in ("execution", "tool_call"):
        writer.add_placement(span_id, node=node, device=_GPU_DEVICE, phase=phase)
    if is_tool and not tool_success:
        writer.add_event(span_id, "tool_error", start_ns, {
            "tool_name": meta.get("tool_name") or meta.get("tool") or ev.get("tool_name"),
            "error": meta.get("error", "unknown")})
    dur = ev.get("duration_ns") or 0
    if is_tool and dur > 5_000_000_000:
        writer.add_event(span_id, "tool_slow", start_ns, {"duration_ns": dur, "tool_name": ev.get("tool_name")})
    return span_id


def _llm_end(ix):
    # type: (dict) -> tuple
    """End of an LLM call: last token (observed), else start + api latency (derived)."""
    start = ix.get("request_start_ns")
    if ix.get("last_token_time_ns") is not None:
        return ix["last_token_time_ns"], "observed", "last_token_time_ns"
    if start is not None and ix.get("api_latency_ms") is not None:
        return start + int(ix["api_latency_ms"] * 1e6), "derived", "api_latency_ms"
    return None, "unknown", "none"


def _build_llm_span(writer, parent_id, ix, node, has_gpu, parent_source="default"):
    # type: (...) -> None
    start_ns = ix.get("request_start_ns")
    if not start_ns:
        return
    end_ns, quality, end_source = _llm_end(ix)
    span_id = writer.record_span(
        "llm_call", "llm:{}".format(ix.get("model_name") or "unknown"), parent_id, start_ns, end_ns,
        span_id=ix.get("span_id"),
        timing_source="llm_interactions:request_start_ns..%s" % end_source, timing_quality=quality,
        parent_source=parent_source, status="error" if ix.get("error_message") else "closed",
        attributes={k: ix.get(k) for k in ("model_name", "prompt_tokens", "completion_tokens",
                                            "api_latency_ms", "ttft_ms", "tpot_ms", "prefill_energy_uj")})
    if span_id is None:
        return
    writer.add_placement(span_id, node=node, device=_CPU_DEVICE, phase="prefill")
    if has_gpu:
        writer.add_placement(span_id, node=node, device=_GPU_DEVICE, phase="decode")
    if ix.get("ttft_ms") and ix["ttft_ms"] > 2000:
        writer.add_event(span_id, "high_ttft", start_ns, {"ttft_ms": ix["ttft_ms"]})
    if ix.get("tpot_ms") and ix["tpot_ms"] > 200:
        writer.add_event(span_id, "low_throughput", start_ns, {"tpot_ms": ix["tpot_ms"]})
    if ix.get("error_message"):
        writer.add_event(span_id, "api_error", start_ns, {"error": ix["error_message"]})
    if ix.get("tcp_retransmits"):
        writer.add_event(span_id, "tcp_retransmit", start_ns, {"tcp_retransmits": ix["tcp_retransmits"]})
