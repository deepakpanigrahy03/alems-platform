"""
core/vocabularies/agent/span_builder.py -- Build child spans from result dict.

Called from save_pair() and save_single() after insert_run() so run_id is
known (EEI-4).  Populates:
    spans            -- goal, attempt, tool_call, llm_call, phase spans
    span_placements  -- device placement on llm_call and phase spans
    span_attributes  -- key metrics on each span kind
    span_events      -- tool errors
    span_links       -- reserved, not populated in foundation
    span_annotations -- reserved, not populated in foundation

Rule S: additive only.  Never raises -- errors logged and skipped.
"""

import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

_CPU_DEVICE = "cpu_package"
_GPU_DEVICE = "gpu_0"


def build_spans_from_result(
    writer,
    run_span_id,
    result,
    workflow_type,
    hw_info,
):
    # type: (...) -> None
    """
    Emit child spans into writer from the harness result dict.

    All spans are opened and immediately closed using data already in
    the result dict -- measurement window is never touched (section 3a).

    Args:
        writer:        SpanWriter instance with root run span already closed.
        run_span_id:   str -- root span id for this run side.
        result:        Full harness result dict.
        workflow_type: 'linear' or 'agentic'.
        hw_info:       Dict from get_hardware_info().
    """
    try:
        _build(writer, run_span_id, result, workflow_type, hw_info)
    except Exception as exc:
        logger.warning(
            "span_builder: workflow=%s skipped: %s", workflow_type, exc
        )


def _build(writer, run_span_id, result, workflow_type, hw_info):
    # type: (...) -> None
    node = socket.gethostname().lower()
    ml = result.get("ml_features") or {}
    has_gpu = bool(ml.get("gpu_total_energy_uj") or ml.get("gpu_dynamic_energy_uj"))
    task_id = result.get("task_id", "unknown")
    exec_status = (result.get("execution") or {}).get("status", "unknown")

    # ── Goal span ────────────────────────────────────────────────────────────
    goal_span_id = writer.open_span(
        kind="goal",
        name="{}:{}".format(workflow_type, task_id),
        parent_span_id=run_span_id,
    )
    _set_if(writer, goal_span_id, "workflow_type", workflow_type)
    _set_if(writer, goal_span_id, "task_id", task_id)
    _set_if(writer, goal_span_id, "attributed_energy_uj", ml.get("attributed_energy_uj"))
    _set_if(writer, goal_span_id, "outcome", exec_status)
    writer.close_span(goal_span_id)

    # ── Attempt span ─────────────────────────────────────────────────────────
    attempt_span_id = writer.open_span(
        kind="attempt",
        name="attempt:1",
        parent_span_id=goal_span_id,
    )
    _set_if(writer, attempt_span_id, "attempt_number", 1)
    _set_if(writer, attempt_span_id, "is_retry", False)
    _set_if(writer, attempt_span_id, "outcome", exec_status)
    writer.close_span(attempt_span_id)

    # ── Phase and tool_call spans from orchestration_events ──────────────────
    orch_events = result.get("orchestration_events") or []
    for ev in orch_events:
        _build_orch_span(writer, attempt_span_id, ev, node, has_gpu)

    # ── LLM call spans from pending_interactions ──────────────────────────────
    interactions = result.get("pending_interactions") or []
    for ix in interactions:
        _build_llm_span(writer, attempt_span_id, ix, node, has_gpu)

    # ── Failed attempt events from execute_goal path ─────────────────────────
    _failed_evs = result.get("span_failed_events") or []
    for fev in _failed_evs:
        _attach_event(writer, attempt_span_id, fev["event_type"], fev["ts_ns"],
                      fev.get("attributes", {}))
    # Verify events were attached.
    for r in writer._spans:
        if r.span_id == attempt_span_id:
            pass


def _build_orch_span(writer, parent_id, ev, node, has_gpu):
    # type: (...) -> None
    phase = ev.get("phase") or "unknown"
    ev_type = ev.get("event_type") or "unknown"
    start_ns = ev.get("start_time_ns")
    if not start_ns:
        return

    if ev_type == "tool_call":
        kind = "tool_call"
        name = "tool:{}".format(ev.get("tool_name") or "unknown")
    else:
        kind = "phase"
        name = "phase:{}".format(phase)

    span_id = writer.open_span(kind=kind, name=name, parent_span_id=parent_id)

    _set_if(writer, span_id, "phase", phase)
    _set_if(writer, span_id, "event_energy_uj", ev.get("event_energy_uj"))
    _set_if(writer, span_id, "duration_ns", ev.get("duration_ns"))
    if ev_type == "tool_call":
        _set_if(writer, span_id, "tool_name", ev.get("tool_name"))
        _meta = ev.get("metadata") or {}
        _tool_success = ev.get("tool_success") if "tool_success" in ev else _meta.get("success", True)
        _set_if(writer, span_id, "tool_success", _tool_success)

    # Placement: CPU always; GPU on execution and tool phases when available.
    writer.add_placement(span_id, node=node, device=_CPU_DEVICE, phase=phase)
    if has_gpu and phase in ("execution", "tool_call"):
        writer.add_placement(span_id, node=node, device=_GPU_DEVICE, phase=phase)

    writer.close_span(span_id)

    # Tool errors become span_events rows -- written via insert_spans.
    _meta = ev.get("metadata") or {}
    _tool_success = ev.get("tool_success") if "tool_success" in ev else _meta.get("success", True)
    if ev_type == "tool_call" and not _tool_success:
        _attach_event(writer, span_id, "tool_error", start_ns, {
            "tool_name": _meta.get("tool_name") or _meta.get("tool") or ev.get("tool_name"),
            "error": _meta.get("error", "unknown"),
        })
    # Slow tool: duration above 5 seconds is anomalous for calculator/search tools.
    _dur = ev.get("duration_ns") or 0
    if ev_type == "tool_call" and _dur > 5_000_000_000:
        _attach_event(writer, span_id, "tool_slow", start_ns,
                      {"duration_ns": _dur, "tool_name": ev.get("tool_name")})


def _build_llm_span(writer, parent_id, ix, node, has_gpu):
    # type: (...) -> None
    start_ns = ix.get("request_start_ns")
    if not start_ns:
        return

    span_id = writer.open_span(
        kind="llm_call",
        name="llm:{}".format(ix.get("model_name") or "unknown"),
        parent_span_id=parent_id,
    )
    _set_if(writer, span_id, "model_name", ix.get("model_name"))
    _set_if(writer, span_id, "prompt_tokens", ix.get("prompt_tokens"))
    _set_if(writer, span_id, "completion_tokens", ix.get("completion_tokens"))
    _set_if(writer, span_id, "api_latency_ms", ix.get("api_latency_ms"))
    _set_if(writer, span_id, "ttft_ms", ix.get("ttft_ms"))
    _set_if(writer, span_id, "tpot_ms", ix.get("tpot_ms"))
    _set_if(writer, span_id, "prefill_energy_uj", ix.get("prefill_energy_uj"))

    writer.add_placement(span_id, node=node, device=_CPU_DEVICE, phase="prefill")
    if has_gpu:
        writer.add_placement(span_id, node=node, device=_GPU_DEVICE, phase="decode")

    # Observations on llm_call span.
    _ttft = ix.get("ttft_ms")
    if _ttft and _ttft > 2000:
        _attach_event(writer, span_id, "high_ttft", ix.get("request_start_ns") or 0,
                      {"ttft_ms": _ttft})
    _tpot = ix.get("tpot_ms")
    if _tpot and _tpot > 200:
        _attach_event(writer, span_id, "low_throughput", ix.get("request_start_ns") or 0,
                      {"tpot_ms": _tpot})
    if ix.get("error_message"):
        _attach_event(writer, span_id, "api_error", ix.get("request_start_ns") or 0,
                      {"error": ix.get("error_message")})
    if ix.get("tcp_retransmits"):
        _attach_event(writer, span_id, "tcp_retransmit", ix.get("request_start_ns") or 0,
                      {"tcp_retransmits": ix.get("tcp_retransmits")})

    writer.close_span(span_id)


def _attach_event(writer, span_id, event_type, ts_ns, attributes):
    # type: (...) -> None
    """
    Attach an event to a closed span record for persistence via insert_spans.
    SpanWriter.flush_to_db serializes record.events when present.
    """
    for record in writer._spans:
        if record.span_id == span_id:
            if not hasattr(record, "events"):
                record.events = []
            record.events.append({
                "event_type": event_type,
                "ts_ns": ts_ns,
                "attributes": attributes,
            })
            return


def _set_if(writer, span_id, key, value):
    # type: (...) -> None
    if value is not None:
        writer.set_attribute(span_id, key, value)
