# core/vocabularies/agent/span_writer.py
# In-memory span builder used by the runner during a measurement window.
#
# Design constraints (section 3a):
# - Inside the measurement window, all operations are list appends only.
#   No I/O, no DB, no locks beyond the list itself.
# - Timestamps use time.perf_counter_ns() which is the same source already
#   used in harness.py for duration computations.
# - Persistence (flush_to_db) is called AFTER insert_run() assigns run_id,
#   matching the EEI-4 pattern used for samples.
# - flush_to_db accepts a DatabaseManager instance, never a raw connection.
#   All writes go through db.insert_spans() (WCC-1, WCC-3).

import time
import uuid
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def _new_span_id() -> str:
    """Generate a globally unique span id as a hex UUID4 string."""
    return uuid.uuid4().hex


class SpanRecord:
    """
    Lightweight in-memory representation of one span.

    Fields map directly to the spans table columns.
    Created by SpanWriter.open_span(); closed by SpanWriter.close_span().
    """

    __slots__ = (
        "span_id", "trace_id", "parent_span_id", "run_id",
        "vocabulary", "vocabulary_version", "kind", "name",
        "start_ns", "end_ns", "start_wall", "status",
        "tenant_kind", "tenant_ref", "provenance_ref",
        "placements", "attributes", "events",
    )

    def __init__(
        self,
        span_id: str,
        trace_id: str,
        parent_span_id: Optional[str],
        kind: str,
        name: str,
        start_ns: int,
        vocabulary: str = "agent",
        vocabulary_version: str = "1",
        tenant_kind: Optional[str] = None,
        tenant_ref: Optional[str] = None,
    ) -> None:
        self.span_id            = span_id
        self.trace_id           = trace_id
        self.parent_span_id     = parent_span_id
        self.run_id             = None           # set by flush_to_db after insert_run
        self.vocabulary         = vocabulary
        self.vocabulary_version = vocabulary_version
        self.kind               = kind
        self.name               = name
        self.start_ns           = start_ns
        self.end_ns             = None           # set by close_span
        self.start_wall         = None           # set on open if caller provides it
        self.status             = "open"
        self.tenant_kind        = tenant_kind
        self.tenant_ref         = tenant_ref
        self.provenance_ref     = None
        self.placements: List[Dict[str, Any]] = []
        self.attributes: Dict[str, Any] = {}
        self.events: List[Dict[str, Any]] = []


class SpanWriter:
    """
    In-memory span accumulator for one run.

    Usage pattern (mirrors sample buffer pattern in EEI-3):
      writer = SpanWriter(trace_id)
      run_span_id = writer.open_span(kind='run', name='gsm8k_basic#42')
      # ... measurement window (list appends only inside) ...
      writer.close_span(run_span_id)
      # after insert_run() returns run_id:
      writer.flush_to_db(db, run_id)   # db is DatabaseManager, never raw conn
    """

    def __init__(self, trace_id: Optional[str] = None) -> None:
        # One trace per experiment session; generate if not provided.
        self.trace_id: str = trace_id or uuid.uuid4().hex
        self._spans: List[SpanRecord] = []
        self._open: Dict[str, SpanRecord] = {}   # span_id -> SpanRecord while open

    def open_span(
        self,
        kind: str,
        name: str,
        parent_span_id: Optional[str] = None,
        tenant_kind: Optional[str] = None,
        tenant_ref: Optional[str] = None,
        vocabulary: str = "agent",
        vocabulary_version: str = "1",
    ) -> str:
        """
        Open a new span.
        Returns the span_id for use in close_span and child spans.
        List-append only; safe inside the measurement window.
        """
        span_id = _new_span_id()
        start_ns = time.perf_counter_ns()
        record = SpanRecord(
            span_id=span_id,
            trace_id=self.trace_id,
            parent_span_id=parent_span_id,
            kind=kind,
            name=name,
            start_ns=start_ns,
            vocabulary=vocabulary,
            vocabulary_version=vocabulary_version,
            tenant_kind=tenant_kind,
            tenant_ref=tenant_ref,
        )
        self._spans.append(record)
        self._open[span_id] = record
        return span_id

    def close_span(self, span_id: str, status: str = "closed") -> None:
        """
        Close an open span.
        end_ns is recorded; status set to closed or error.
        List mutation only; safe inside the measurement window.
        """
        record = self._open.pop(span_id, None)
        if record is None:
            # Already closed or never opened; log and ignore.
            logger.warning("SpanWriter: close_span called on unknown span_id %s", span_id)
            return
        record.end_ns = time.perf_counter_ns()
        record.status = status if status in ("closed", "error") else "closed"

    def add_placement(
        self,
        span_id: str,
        node: str,
        device: str,
        phase: Optional[str] = None,
    ) -> None:
        """
        Add a device placement to an open span.
        List-append only; safe inside measurement window.
        """
        record = self._open.get(span_id)
        if record is None:
            logger.warning("SpanWriter: add_placement on unknown/closed span %s", span_id)
            return
        start_ns = time.perf_counter_ns()
        record.placements.append({
            "node": node, "device": device,
            "phase": phase, "start_ns": start_ns, "end_ns": None,
        })

    def set_attribute(self, span_id: str, key: str, value: Any) -> None:
        """
        Set an attribute on an open span.
        Attributes are immutable after close (INV-10); enforces open-only writes.
        """
        record = self._open.get(span_id)
        if record is None:
            logger.warning("SpanWriter: set_attribute on unknown/closed span %s", span_id)
            return
        record.attributes[key] = value

    def flush_to_db(self, db, run_id: int) -> int:
        """
        Persist all accumulated spans through DatabaseManager (WCC-1, WCC-3).
        Called AFTER insert_run() so run_id is available (EEI-4).

        Args:
            db:     DatabaseManager instance — same object used by save_pair/save_single.
                    Never a raw sqlite3 connection.
            run_id: Assigned by insert_run(); links spans to the runs table.

        Returns:
            Number of span rows flushed.
        """

        # Force-close any spans still open (should not happen in normal flow).
        for span_id, record in list(self._open.items()):
            logger.warning(
                "SpanWriter.flush_to_db: span %s (kind=%s) still open at flush; force-closing",
                span_id, record.kind,
            )
            record.end_ns = time.perf_counter_ns()
            record.status = "error"
        self._open.clear()

        if not self._spans:
            return 0

        # Serialize spans to plain dicts for DatabaseManager.insert_spans.
        # No raw conn access here (WCC-1, WCC-3).
        records = []
        for record in self._spans:
            record.run_id = run_id
            records.append({
                "span_id":            record.span_id,
                "trace_id":           record.trace_id,
                "parent_span_id":     record.parent_span_id,
                "vocabulary":         record.vocabulary,
                "vocabulary_version": record.vocabulary_version,
                "kind":               record.kind,
                "name":               record.name,
                "start_ns":           record.start_ns,
                "end_ns":             record.end_ns,
                "start_wall":         record.start_wall,
                "status":             record.status,
                "tenant_kind":        record.tenant_kind,
                "tenant_ref":         record.tenant_ref,
                "placements":         record.placements,
                "attributes":         record.attributes,
                "events":             getattr(record, "events", []),
            })


        try:
            db.insert_spans(run_id, records)
        except Exception as exc:
            # Span persistence must never abort the run (Rule S, additive only).
            logger.warning("SpanWriter.flush_to_db: insert_spans failed: %s", exc)
            return 0

        return len(records)


def _encode_attribute(value: Any):
    # type: (Any) -> tuple
    """
    Encode a Python value for span_attributes storage.

    Returns:
        (value_type, value_text) tuple.
    """
    import json  # noqa: PLC0415 -- deferred to avoid module-level cost
    if isinstance(value, bool):
        return ("bool", "true" if value else "false")
    if isinstance(value, int):
        return ("int", str(value))
    if isinstance(value, float):
        return ("float", str(value))
    if isinstance(value, str):
        return ("string", value)
    # Fall back to JSON for dicts, lists, etc.
    try:
        return ("json", json.dumps(value))
    except Exception:
        return ("string", str(value))
