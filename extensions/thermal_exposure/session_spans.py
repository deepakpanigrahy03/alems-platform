"""
In-memory span tree of one experiment recording.

The recorder opens and closes spans at the instants it already knows (open,
pause at a run start, resume at a run end, close). Spans are kept here and
written by the recorder's flush, which runs only between runs, so no span
write ever happens during a run. Every change marks the span dirty; the
flush writes dirty spans as one upsert batch through the injected writer.

Imports only alems_sdk and this extension (no core imports).
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from alems_sdk.spans import SpanContractError, SpanVocabulary, encode_attribute

# placement device for spans that cover the whole machine
DEVICE_SYSTEM = "system"

# crash recovery (run inside the same transaction that marks the recording
# aborted). Only spans still 'open' change, so running it twice changes
# nothing more. A span whose end is already known (a finished run slot)
# keeps it; others end at the last stored sample, else at the session start.
RECOVER_SPANS_SQL = (
    "UPDATE spans SET status = 'error', end_ns = COALESCE(end_ns, "
    "  (SELECT MAX(timestamp_ns) FROM te_thermal_samples WHERE session_id = ?), "
    "  (SELECT start_ns FROM te_session WHERE session_id = ?)) "
    "WHERE vocabulary = 'thermal_session' AND status = 'open' "
    "  AND trace_id = (SELECT global_session_id FROM te_session WHERE session_id = ?)")
RECOVER_PLACEMENTS_SQL = (
    "UPDATE span_placements SET end_ns = "
    "  (SELECT s.end_ns FROM spans s WHERE s.span_id = span_placements.span_id) "
    "WHERE end_ns IS NULL AND span_id IN (SELECT span_id FROM spans "
    "  WHERE vocabulary = 'thermal_session' "
    "  AND trace_id = (SELECT global_session_id FROM te_session WHERE session_id = ?))")

_UPSERT_SPAN = (
    "INSERT INTO spans (span_id, trace_id, parent_span_id, run_id, vocabulary, "
    "vocabulary_version, kind, name, start_ns, end_ns, status) "
    "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(span_id) DO UPDATE SET end_ns = excluded.end_ns, status = excluded.status")
_INSERT_PLACEMENT = ("INSERT INTO span_placements (span_id, node, device, phase, start_ns, end_ns) "
                     "VALUES (?, ?, ?, NULL, ?, ?)")
_UPDATE_PLACEMENT = "UPDATE span_placements SET end_ns = ? WHERE span_id = ?"
_UPSERT_ATTR = ("INSERT OR REPLACE INTO span_attributes (span_id, key, value_text, value_type) "
                "VALUES (?, ?, ?, ?)")


class _Span:
    """One span and its write state."""
    __slots__ = ("span_id", "kind", "name", "parent_id", "start_ns", "end_ns",
                 "status", "attrs", "written", "dirty")

    def __init__(self, kind: str, name: str, parent_id: Optional[str], start_ns: int) -> None:
        """A new open span; dirty so the next flush writes it."""
        self.span_id = uuid.uuid4().hex
        self.kind, self.name, self.parent_id = kind, name, parent_id
        self.start_ns, self.end_ns = start_ns, None   # type: int, Optional[int]
        self.status = "open"
        self.attrs: Dict[str, Any] = {}
        self.written = False        # placement row exists in the store
        self.dirty = True


class SessionSpans:
    """
    Span tree of one recording, checked against its vocabulary.

    Args:
        vocab: the SpanVocabulary every span must satisfy.
        trace_id: global_session_id of the recording (one trace per recording).
        node: logical node name for placements (never a hostname).
    """

    def __init__(self, vocab: SpanVocabulary, trace_id: str, node: str,
                 device: str = DEVICE_SYSTEM) -> None:
        """Empty tree; pause/resume run on the harness thread, flush on its own."""
        self.vocab, self.trace_id, self.node, self.device = vocab, trace_id, node, device
        self._lock = threading.Lock()
        self._spans: Dict[str, _Span] = {}

    def open(self, kind: str, name: str, start_ns: int,
             parent_id: Optional[str] = None) -> str:
        """Open a span after the vocabulary accepts kind and parent; returns its id."""
        with self._lock:
            parent = self._spans.get(parent_id) if parent_id else None
            if parent_id and parent is None:
                raise SpanContractError("unknown parent span %s" % parent_id)
            self.vocab.validate_open(kind, parent.kind if parent else None)
            span = _Span(kind, name, parent_id, start_ns)
            self._spans[span.span_id] = span
            return span.span_id

    def set_end(self, span_id: str, end_ns: int) -> None:
        """Record the end of a span whose attributes come later (run slots)."""
        with self._lock:
            span = self._get(span_id)
            self._check_end(span, end_ns)
            span.end_ns, span.dirty = end_ns, True

    def close(self, span_id: str, end_ns: Optional[int], attrs: Dict[str, Any],
              status: str = "closed") -> None:
        """
        Close a span. end_ns None keeps the end set earlier by set_end().

        Attributes are checked by the vocabulary and fixed from here on
        (closed spans are immutable).
        """
        with self._lock:
            span = self._get(span_id)
            if span.status != "open":
                raise SpanContractError("span %s (%s) already closed" % (span_id, span.kind))
            end = span.end_ns if end_ns is None else end_ns
            self._check_end(span, end)
            self.vocab.validate_close(span.kind, attrs)
            span.end_ns, span.attrs = end, {k: v for k, v in attrs.items() if v is not None}
            span.status = status if status in ("closed", "error") else "closed"
            span.dirty = True

    def start_of(self, span_id: str) -> int:
        """Start time of a span."""
        with self._lock:
            return self._get(span_id).start_ns

    def drain(self) -> List[Dict[str, Any]]:
        """Snapshots of every dirty span (marked clean); requeue() on a failed write."""
        with self._lock:
            items = [self._snapshot(s) for s in self._spans.values() if s.dirty]
            for s in self._spans.values():
                s.dirty = False
            return items

    def requeue(self, items: List[Dict[str, Any]]) -> None:
        """A write failed: mark these spans dirty again for the next flush."""
        with self._lock:
            for it in items:
                self._spans[it["span_id"]].dirty = True

    def mark_written(self, items: List[Dict[str, Any]]) -> None:
        """A write succeeded: placement rows of these spans now exist."""
        with self._lock:
            for it in items:
                self._spans[it["span_id"]].written = True

    def statements(self, items: List[Dict[str, Any]]) -> List[Tuple[str, tuple]]:
        """SQL statements (one batch) that store the given snapshots."""
        out: List[Tuple[str, tuple]] = []
        for it in items:
            out.append((_UPSERT_SPAN, (it["span_id"], self.trace_id, it["parent_id"],
                                       self.vocab.vocabulary_id, self.vocab.version,
                                       it["kind"], it["name"], it["start_ns"],
                                       it["end_ns"], it["status"])))
            out.append(self._placement(it))
            out.extend(self._attributes(it))
        return out

    # ── helpers ──────────────────────────────────────────────────────────────

    def tree(self) -> List[Dict[str, Any]]:
        """Every span in the shape partition_problems() reads, plus attributes."""
        with self._lock:
            return [{"span_id": s.span_id, "parent_span_id": s.parent_id, "kind": s.kind,
                     "start_ns": s.start_ns, "end_ns": s.end_ns, "status": s.status,
                     "attrs": dict(s.attrs)} for s in self._spans.values()]

    def _get(self, span_id: str) -> _Span:
        """The span, or a contract error for an unknown id."""
        span = self._spans.get(span_id)
        if span is None:
            raise SpanContractError("unknown span %s" % span_id)
        return span

    @staticmethod
    def _check_end(span: _Span, end_ns: Optional[int]) -> None:
        """An end must exist and must not come before the start."""
        if end_ns is None or end_ns < span.start_ns:
            raise SpanContractError("span %s (%s): end %s before start %s"
                                    % (span.span_id, span.kind, end_ns, span.start_ns))

    @staticmethod
    def _snapshot(s: _Span) -> Dict[str, Any]:
        """Copy of the fields a write needs (the span may change after drain)."""
        return {"span_id": s.span_id, "kind": s.kind, "name": s.name,
                "parent_id": s.parent_id, "start_ns": s.start_ns, "end_ns": s.end_ns,
                "status": s.status, "attrs": dict(s.attrs), "written": s.written}

    def _placement(self, it: Dict[str, Any]) -> Tuple[str, tuple]:
        """First write inserts the placement; later writes update its end."""
        if it["written"]:
            return _UPDATE_PLACEMENT, (it["end_ns"], it["span_id"])
        return _INSERT_PLACEMENT, (it["span_id"], self.node, self.device,
                                   it["start_ns"], it["end_ns"])

    def _attributes(self, it: Dict[str, Any]) -> List[Tuple[str, tuple]]:
        """Attribute rows; written only once the span is closed (fixed values)."""
        if it["status"] == "open":
            return []
        specs = self.vocab.kinds[it["kind"]].attributes
        rows = []
        for key, value in sorted(it["attrs"].items()):
            text, vtype = encode_attribute(specs[key], value)
            rows.append((_UPSERT_ATTR, (it["span_id"], key, text, vtype)))
        return rows


# ── checks (recorder, integrity checker and tests share these) ──────────────

def partition_problems(rows: List[Dict[str, Any]]) -> List[str]:
    """
    Structure problems of one recording's spans; empty list = correct.

    rows: dicts with span_id, parent_span_id, kind, start_ns, end_ns, status.
    Checks: exactly one session; the top-level spans tile the session with
    no gap and no overlap; run slots and gaps tile each workload; no span
    left open in a closed tree.
    """
    roots = [r for r in rows if r["kind"] == "session"]
    if len(roots) != 1:
        return ["expected one session span, found %d" % len(roots)]
    root = roots[0]
    problems = _tiles(root, _children(rows, root["span_id"]), "session")
    for wl in (r for r in rows if r["kind"] == "workload"):
        kids = [r for r in _children(rows, wl["span_id"]) if r["kind"] in ("run_slot", "gap")]
        problems += _tiles(wl, kids, "workload")
    if root["status"] != "open":
        problems += ["span %s (%s) still open" % (r["span_id"], r["kind"])
                     for r in rows if r["status"] == "open"]
    return problems


def _children(rows: List[Dict[str, Any]], parent_id: str) -> List[Dict[str, Any]]:
    """Direct children of a span, by start time."""
    return sorted((r for r in rows if r["parent_span_id"] == parent_id),
                  key=lambda r: (r["start_ns"], r["end_ns"] or 0))


def _tiles(parent: Dict[str, Any], kids: List[Dict[str, Any]], label: str) -> List[str]:
    """Problems if kids do not cover parent exactly, end to start, with no gap or overlap."""
    if not kids:
        return ["%s: no child spans" % label]
    out = []
    if kids[0]["start_ns"] != parent["start_ns"]:
        out.append("%s: first child starts %s, parent %s"
                   % (label, kids[0]["start_ns"], parent["start_ns"]))
    for a, b in zip(kids, kids[1:]):
        if a["end_ns"] != b["start_ns"]:
            kind = "overlap" if (a["end_ns"] or 0) > b["start_ns"] else "gap"
            out.append("%s: %s between %s (ends %s) and %s (starts %s)"
                       % (label, kind, a["kind"], a["end_ns"], b["kind"], b["start_ns"]))
    if kids[-1]["end_ns"] != parent["end_ns"]:
        out.append("%s: last child ends %s, parent %s"
                   % (label, kids[-1]["end_ns"], parent["end_ns"]))
    return out
