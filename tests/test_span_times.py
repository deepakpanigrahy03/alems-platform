"""45.8 step 1: spans carry source times, never the time of building (no GPU, no store)."""
import time

import pytest

from core.vocabularies.agent import span_builder as sb
from core.vocabularies.agent.span_writer import SpanWriter

S = 1_000_000_000
T = 1_790_000_000 * S   # an epoch time in the past


def _result():
    return {
        "task_id": "t3", "execution": {"status": "success"},
        "ml_features": {"start_time_ns": T, "end_time_ns": T + 70 * S, "pre_task_duration_ns": S // 10,
                        "task_duration_ns": 69 * S},
        "orchestration_events": [
            {"phase": "planning", "event_type": "phase", "start_time_ns": T + S, "end_time_ns": T + 6 * S},
            {"phase": "execution", "event_type": "tool_call", "tool_name": "calc", "start_time_ns": T + 7 * S,
             "duration_ns": 2 * S, "tool_success": False}],
        "pending_interactions": [
            {"model_name": "m", "request_start_ns": T + 2 * S, "last_token_time_ns": T + 5 * S},
            {"model_name": "m", "request_start_ns": T + 10 * S, "api_latency_ms": 1500.0},
            {"model_name": "m", "request_start_ns": T + 20 * S}],
    }


def test_spans_use_source_times_not_build_time(monkeypatch):
    calls = []
    monkeypatch.setattr(time, "time_ns", lambda: calls.append(1) or 0)
    monkeypatch.setattr(time, "perf_counter_ns", lambda: calls.append(1) or 0)
    w = SpanWriter("tr")
    ids = sb.record_goal_tree(w, "agentic", "t3", _result(), "success")
    rid = ids["run"]
    sb.build_spans_from_result(w, rid, _result(), "agentic", {})
    assert calls == []                      # building read no clock
    by = {(r.kind, r.name): r for r in w._spans}
    run = by[("run", "agentic:t3")]
    assert (run.start_ns, run.end_ns) == (T, T + 70 * S)
    assert [e["event_type"] for e in run.events] == ["task_start", "task_end"]
    assert run.events[0]["ts_ns"] == T + S // 10
    plan = by[("phase", "phase:planning")]
    assert (plan.start_ns, plan.end_ns, plan.timing_quality) == (T + S, T + 6 * S, "observed")
    tool = by[("tool_call", "tool:calc")]
    assert (tool.end_ns, tool.timing_quality, tool.status) == (T + 9 * S, "derived", "error")
    llms = sorted((r for r in w._spans if r.kind == "llm_call"), key=lambda r: r.start_ns)
    assert [(r.end_ns, r.timing_quality) for r in llms] == [
        (T + 5 * S, "observed"), (T + 10 * S + 1_500_000_000, "derived"), (None, "unknown")]


def test_record_span_rules():
    w = SpanWriter("tr")
    assert w.record_span("phase", "p", None, None, 5, timing_source="x") is None   # no start: not recorded
    sid = w.record_span("phase", "p", None, 1, None, timing_source="x")
    assert w.get(sid).timing_quality == "unknown" and w.get(sid).end_ns is None
    with pytest.raises(ValueError):
        w.record_span("phase", "p", None, 1, 2, timing_source="x", timing_quality="guess")


def test_live_span_uses_wall_and_monotonic_and_notifies(monkeypatch):
    from core.observability import span_listeners as sl
    seen = []

    class L:
        def on_open(self, s): seen.append(("open", s["span_id"]))
        def on_close(self, s): seen.append(("close", s["span_id"]))
    monkeypatch.setattr(sl, "_LISTENERS", [L()])
    w = SpanWriter("tr")
    before = time.time_ns()
    sid = w.open_span("phase", "live")
    w.close_span(sid)
    r = w.get(sid)
    assert before <= r.start_ns <= r.end_ns and r.start_mono_ns <= r.end_mono_ns
    assert seen == [("open", sid), ("close", sid)]
    w.record_span("phase", "rebuilt", None, 1, 2, timing_source="x")
    assert len(seen) == 2                   # record_span never notifies


def test_open_at_flush_keeps_end_unknown():
    class DB:
        def insert_spans(self, run_id, recs): self.recs = recs
    w, db = SpanWriter("tr"), DB()
    w.open_span("phase", "never closed")
    w.flush_to_db(db, 1)
    r = db.recs[0]
    assert r["end_ns"] is None and r["status"] == "error" and r["timing_quality"] == "unknown"


def test_tree_goal_attempt_run_and_inferred_parents():
    w = SpanWriter("tr")
    ids = sb.record_goal_tree(w, "agentic", "t3", _result(), "success")
    sb.build_spans_from_result(w, ids["run"], _result(), "agentic", {})
    by = {r.span_id: r for r in w._spans}
    assert by[ids["goal"]].parent_span_id is None
    assert by[ids["attempt"]].parent_span_id == ids["goal"]
    assert by[ids["run"]].parent_span_id == ids["attempt"]
    plan = next(r for r in w._spans if r.name == "phase:planning")
    assert plan.parent_span_id == ids["run"]
    llms = sorted((r for r in w._spans if r.kind == "llm_call"), key=lambda r: r.start_ns)
    # 2 s .. 5 s lies inside planning (1 s .. 6 s): inferred; 10 s call: no containing phase
    assert (llms[0].parent_span_id, llms[0].parent_source) == (plan.span_id, "inferred")
    assert (llms[1].parent_span_id, llms[1].parent_source) == (ids["run"], "default")
    tool = next(r for r in w._spans if r.kind == "tool_call")
    assert tool.parent_span_id == ids["run"] and tool.parent_source == "default"
    assert sum(r.kind == "goal" for r in w._spans) == 1 and sum(r.kind == "attempt" for r in w._spans) == 1


def test_container_ambiguous_is_default():
    assert sb._container([(0, 10, "a"), (0, 10, "b")], 2, 3, "run") == ("run", "default")
    assert sb._container([(0, 10, "a"), (1, 9, "b")], 2, 3, "run") == ("b", "inferred")
    assert sb._container([(0, 10, "a")], 2, None, "run") == ("run", "default")


def test_live_ids_give_one_span_per_operation_with_observed_parent(monkeypatch):
    from core.observability import span_listeners as sl
    from core.observability.live_spans import LiveSpans
    seen = []

    class L:
        def on_open(self, s): seen.append(("open", s["kind"], s["span_id"]))
        def on_close(self, s): seen.append(("close", s["kind"], s["span_id"]))
    monkeypatch.setattr(sl, "_LISTENERS", [L()])
    live = LiveSpans()
    r = _result()
    plan = live.begin_phase("planning")
    llm = live.begin("llm_call", "llm:m", live.phase)
    live.end(llm)
    live.end_phase()
    r["orchestration_events"][0].update(span_id=plan)
    r["pending_interactions"][0].update(span_id=llm, parent_span_id=plan)
    r["pending_interactions"][1].update(span_id="x10", parent_span_id=None)   # outside any phase
    w = SpanWriter("tr")
    ids = sb.record_goal_tree(w, "agentic", "t3", r, "success")
    sb.build_spans_from_result(w, ids["run"], r, "agentic", {})
    assert w.get(plan).name == "phase:planning"                        # stored with the live id
    assert (w.get(llm).parent_span_id, w.get(llm).parent_source) == (plan, "observed")
    assert (w.get("x10").parent_span_id, w.get("x10").parent_source) == (ids["run"], "observed")
    assert [x[:2] for x in seen] == [("open", "phase"), ("open", "llm_call"), ("close", "llm_call"),
                                     ("close", "phase")]
    assert sum(1 for x in w._spans if x.span_id == llm) == 1
