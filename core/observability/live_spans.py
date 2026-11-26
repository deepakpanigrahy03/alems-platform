"""
core/observability/live_spans.py: span ids handed out at the moment an operation starts.

Why (45.8 D): a span's identity and its parent must be known while the work happens,
not reconstructed afterwards. The agent asks for an id when a phase, a tool call or an
LLM call starts; listeners (for example NVTX) are told at that moment and again at the
end. Nothing is written here: the id travels with the orchestration event or LLM
interaction to persistence, where the span is recorded once with that id and the
measured times (SpanWriter.record_span). So the stored span and the profiler range are
the same operation, and the parent is observed, not derived from timestamps.

Inside the measurement window: dict and list operations only; listener calls are the
cheap, never raising calls of core.observability.span_listeners.
"""

import time
import types
import uuid
from typing import Dict, Optional


class LiveSpans:
    """Live span ids for one agent execution; tracks the current phase."""

    def __init__(self):
        self.phase = None   # type: Optional[str]   span id of the phase running now
        self._open = {}     # type: Dict[str, types.SimpleNamespace]
        # Load listeners now (entry points, settings, NVTX plugin), never inside the
        # first phase between its stored start and its profiler range (45.4d).
        try:
            from core.observability import span_listeners
            span_listeners.listeners()
        except Exception:  # observability never breaks the run
            pass

    def begin(self, kind, name, parent_span_id=None):
        # type: (str, str, Optional[str]) -> str
        """New span id for an operation starting now; listeners are told now."""
        rec = types.SimpleNamespace(span_id=uuid.uuid4().hex, trace_id=None, parent_span_id=parent_span_id,
                                    kind=kind, name=name, start_ns=time.time_ns(), end_ns=None, status="open")
        self._open[rec.span_id] = rec
        from core.observability import span_listeners
        span_listeners.on_open(rec)
        return rec.span_id

    def end(self, span_id, status="closed"):
        # type: (Optional[str], str) -> None
        """The operation ended now; listeners are told. Unknown ids are ignored."""
        rec = self._open.pop(span_id, None) if span_id else None
        if rec is None:
            return
        rec.end_ns, rec.status = time.time_ns(), status
        from core.observability import span_listeners
        span_listeners.on_close(rec)

    def begin_phase(self, name):
        # type: (str) -> str
        self.phase = self.begin("phase", "phase:%s" % name)
        return self.phase

    def end_phase(self):
        # type: () -> Optional[str]
        sid, self.phase = self.phase, None
        self.end(sid)
        return sid
