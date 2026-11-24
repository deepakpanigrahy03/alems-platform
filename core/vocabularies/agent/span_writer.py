# core/vocabularies/agent/span_writer.py
# In-memory span accumulator; persisted after the run row exists (EEI-4).
#
# Two ways to create a span (45.8, review decision A):
# - open_span / close_span: LIVE capture of an operation happening now. Reads the
#   wall clock (time.time_ns, the clock of runs and energy samples) and the
#   monotonic clock (time.monotonic_ns, for elapsed time and wall clock jumps),
#   and notifies span listeners (for example NVTX). List appends only, safe
#   inside the measurement window.
# - record_span: RECONSTRUCTION of an operation from stored or result data. Never
#   reads a clock, never notifies listeners. Times come from the source; an
#   unknown end stays NULL with timing_quality 'unknown', never "now".
# Span status is the execution outcome (open, closed, error); timing_quality
# (observed, derived, unknown) and timing_source say how the times were obtained.
# flush_to_db accepts a DatabaseManager, never a raw connection (WCC-1, WCC-3).

import time
import uuid
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

TIMING_QUALITY = ("observed", "derived", "unknown")


def _new_span_id() -> str:
    """Globally unique span id (hex UUID4)."""
    return uuid.uuid4().hex


class SpanRecord:
    """One span; fields map to the spans table columns."""

    __slots__ = (
        "span_id", "trace_id", "parent_span_id", "run_id",
        "vocabulary", "vocabulary_version", "kind", "name",
        "start_ns", "end_ns", "start_mono_ns", "end_mono_ns", "start_wall", "status",
        "timing_quality", "timing_source", "parent_source",
        "tenant_kind", "tenant_ref", "provenance_ref",
        "placements", "attributes", "events",
    )

    def __init__(self, span_id, trace_id, parent_span_id, kind, name, start_ns,
                 vocabulary="agent", vocabulary_version="1", tenant_kind=None, tenant_ref=None):
        # type: (str, str, Optional[str], str, str, int, str, str, Optional[str], Optional[str]) -> None
        self.span_id = span_id
        self.trace_id = trace_id
        self.parent_span_id = parent_span_id
        self.run_id = None              # set by flush_to_db
        self.vocabulary = vocabulary
        self.vocabulary_version = vocabulary_version
        self.kind = kind
        self.name = name
        self.start_ns = start_ns        # wall clock epoch ns
        self.end_ns = None              # wall clock epoch ns, NULL when unknown
        self.start_mono_ns = None       # monotonic ns, only when observed live
        self.end_mono_ns = None
        self.start_wall = None
        self.status = "open"
        self.timing_quality = "observed"
        self.timing_source = None
        self.parent_source = "observed"
        self.tenant_kind = tenant_kind
        self.tenant_ref = tenant_ref
        self.provenance_ref = None
        self.placements = []            # type: List[Dict[str, Any]]
        self.attributes = {}            # type: Dict[str, Any]
        self.events = []                # type: List[Dict[str, Any]]


class SpanWriter:
    """
    In-memory span accumulator.

      writer = SpanWriter(trace_id)
      sid = writer.record_span("run", "agentic:t3", None, start_ns, end_ns, timing_source="runs")
      ...                                   # or open_span/close_span for live operations
      writer.flush_to_db(db, run_id)        # after the run row exists
    """

    def __init__(self, trace_id=None):
        # type: (Optional[str]) -> None
        self.trace_id = trace_id or uuid.uuid4().hex   # type: str
        self._spans = []    # type: List[SpanRecord]
        self._open = {}     # type: Dict[str, SpanRecord]
        self._by_id = {}    # type: Dict[str, SpanRecord]

    # ── live capture ────────────────────────────────────────────────────────
    def open_span(self, kind, name, parent_span_id=None, tenant_kind=None, tenant_ref=None,
                  vocabulary="agent", vocabulary_version="1", span_id=None):
        # type: (str, str, Optional[str], Optional[str], Optional[str], str, str, Optional[str]) -> str
        """Open a span for an operation starting now (live). Notifies listeners."""
        record = SpanRecord(span_id or _new_span_id(), self.trace_id, parent_span_id, kind, name,
                            time.time_ns(), vocabulary, vocabulary_version, tenant_kind, tenant_ref)
        record.start_mono_ns = time.monotonic_ns()
        record.timing_source = "live"
        self._add(record)
        self._open[record.span_id] = record
        from core.observability import span_listeners
        span_listeners.on_open(record)
        return record.span_id

    def close_span(self, span_id, status="closed"):
        # type: (str, str) -> None
        """Close a live span now. Notifies listeners."""
        record = self._open.pop(span_id, None)
        if record is None:
            logger.warning("SpanWriter: close_span called on unknown span_id %s", span_id)
            return
        record.end_ns = time.time_ns()
        record.end_mono_ns = time.monotonic_ns()
        record.status = status if status in ("closed", "error") else "closed"
        from core.observability import span_listeners
        span_listeners.on_close(record)

    # ── reconstruction ──────────────────────────────────────────────────────
    def record_span(self, kind, name, parent_span_id, start_ns, end_ns, timing_source,
                    timing_quality=None, parent_source="observed", status="closed", span_id=None,
                    start_mono_ns=None, end_mono_ns=None, attributes=None, vocabulary="agent",
                    vocabulary_version="1"):
        # type: (...) -> Optional[str]
        """
        Record a span whose times come from a source (never from a clock).

        start_ns is required: an operation without a known start cannot be placed and is
        not recorded (logged; returns None). An unknown end stays None with
        timing_quality 'unknown'. Listeners are not notified.
        """
        if start_ns is None:
            logger.warning("SpanWriter.record_span: %s %r has no known start; not recorded", kind, name)
            return None
        record = SpanRecord(span_id or _new_span_id(), self.trace_id, parent_span_id, kind, name,
                            int(start_ns), vocabulary, vocabulary_version)
        record.end_ns = int(end_ns) if end_ns is not None else None
        record.start_mono_ns, record.end_mono_ns = start_mono_ns, end_mono_ns
        record.timing_source = timing_source
        record.timing_quality = timing_quality or ("observed" if end_ns is not None else "unknown")
        if record.timing_quality not in TIMING_QUALITY:
            raise ValueError("timing_quality %r not in %s" % (record.timing_quality, TIMING_QUALITY))
        record.parent_source = parent_source
        record.status = status if status in ("closed", "error") else "closed"
        record.attributes.update({k: v for k, v in (attributes or {}).items() if v is not None})
        self._add(record)
        return record.span_id

    # ── details ─────────────────────────────────────────────────────────────
    def add_placement(self, span_id, node, device, phase=None):
        # type: (str, str, str, Optional[str]) -> None
        """Device placement over the span's own interval (live: from now until close)."""
        record = self._by_id.get(span_id)
        if record is None:
            logger.warning("SpanWriter: add_placement on unknown span %s", span_id)
            return
        live = span_id in self._open
        record.placements.append({"node": node, "device": device, "phase": phase,
                                  "start_ns": time.time_ns() if live else record.start_ns,
                                  "end_ns": None if live else record.end_ns})

    def set_attribute(self, span_id, key, value):
        # type: (str, str, Any) -> None
        """Attribute on an open live span, or on a recorded span (not yet flushed)."""
        record = self._by_id.get(span_id)
        if record is None:
            logger.warning("SpanWriter: set_attribute on unknown span %s", span_id)
            return
        if record.run_id is not None:
            logger.warning("SpanWriter: set_attribute after flush on %s ignored (INV-10)", span_id)
            return
        record.attributes[key] = value

    def add_event(self, span_id, event_type, ts_ns, attributes=None):
        # type: (str, str, Optional[int], Optional[Dict[str, Any]]) -> None
        """Event on a span at a source time (ts_ns None when unknown)."""
        record = self._by_id.get(span_id)
        if record is None:
            logger.warning("SpanWriter: add_event on unknown span %s", span_id)
            return
        record.events.append({"event_type": event_type, "ts_ns": ts_ns, "attributes": attributes or {}})

    def get(self, span_id):
        # type: (str) -> Optional[SpanRecord]
        return self._by_id.get(span_id)

    def _add(self, record):
        # type: (SpanRecord) -> None
        self._spans.append(record)
        self._by_id[record.span_id] = record

    # ── persistence ─────────────────────────────────────────────────────────
    def flush_to_db(self, db, run_id):
        # type: (Any, int) -> int
        """
        Persist all spans through DatabaseManager (WCC-1, WCC-3), after the run row
        exists (EEI-4). A live span still open is stored with status error and an
        unknown end (no clock is read to fake one).
        """
        for span_id, record in list(self._open.items()):
            logger.warning("SpanWriter.flush_to_db: span %s (kind=%s) still open; end unknown",
                           span_id, record.kind)
            record.status, record.timing_quality = "error", "unknown"
        self._open.clear()
        if not self._spans:
            return 0
        records = []
        for record in self._spans:
            record.run_id = run_id
            records.append({
                "span_id": record.span_id, "trace_id": record.trace_id,
                "parent_span_id": record.parent_span_id, "vocabulary": record.vocabulary,
                "vocabulary_version": record.vocabulary_version, "kind": record.kind,
                "name": record.name, "start_ns": record.start_ns, "end_ns": record.end_ns,
                "start_mono_ns": record.start_mono_ns, "end_mono_ns": record.end_mono_ns,
                "start_wall": record.start_wall, "status": record.status,
                "timing_quality": record.timing_quality, "timing_source": record.timing_source,
                "parent_source": record.parent_source,
                "tenant_kind": record.tenant_kind, "tenant_ref": record.tenant_ref,
                "placements": record.placements, "attributes": record.attributes,
                "events": record.events,
            })
        try:
            db.insert_spans(run_id, records)
        except Exception as exc:
            # Span persistence never aborts the run (Rule S, additive only).
            logger.warning("SpanWriter.flush_to_db: insert_spans failed: %s", exc)
            return 0
        return len(records)


def _encode_attribute(value):
    # type: (Any) -> tuple
    """(value_type, value_text) for span_attributes storage."""
    import json  # noqa: PLC0415 -- deferred to avoid module-level cost
    if isinstance(value, bool):
        return ("bool", "true" if value else "false")
    if isinstance(value, int):
        return ("int", str(value))
    if isinstance(value, float):
        return ("float", str(value))
    if isinstance(value, str):
        return ("string", value)
    try:
        return ("json", json.dumps(value))
    except Exception:
        return ("string", str(value))
