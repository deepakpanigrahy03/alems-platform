-- v113_span_tables.sql
-- Span storage: spans, placements, links, events, attributes, annotations.
-- All tables are additive. No existing table is altered here.
-- span_id on existing tables is in v114.

CREATE TABLE IF NOT EXISTS spans (
    span_id          TEXT    NOT NULL PRIMARY KEY,   -- globally unique, UUID or hex
    trace_id         TEXT    NOT NULL,               -- groups spans across a run or session
    parent_span_id   TEXT,                           -- NULL for root span of a run
    run_id           INTEGER,                        -- FK to runs; NULL for pre-insert spans
    vocabulary       TEXT    NOT NULL DEFAULT 'generic',  -- registered vocabulary id
    vocabulary_version TEXT  NOT NULL DEFAULT '1',
    kind             TEXT    NOT NULL,               -- experiment|run|goal|attempt|turn|llm_call|tool_call|phase|retry|recovery|job|task|function
    name             TEXT    NOT NULL,
    start_ns         INTEGER NOT NULL,               -- monotonic nanoseconds
    end_ns           INTEGER,                        -- NULL while open
    start_wall       TEXT,                           -- ISO-8601 wall clock at open
    status           TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'error')),
    tenant_kind      TEXT,                           -- process|cgroup|container|pod|none
    tenant_ref       TEXT,                           -- pid, cgroup path, container id, etc.
    provenance_ref   TEXT,                           -- references provenance table
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_spans_trace    ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_run      ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent   ON spans(parent_span_id);
CREATE INDEX IF NOT EXISTS idx_spans_kind     ON spans(kind);

-- One row per (span, device, phase) placement interval.
-- A single llm_call span may appear on CPU and GPU simultaneously.
CREATE TABLE IF NOT EXISTS span_placements (
    placement_id     INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    span_id          TEXT    NOT NULL,
    node             TEXT    NOT NULL,               -- hostname
    device           TEXT    NOT NULL,               -- cpu_pkg_0|gpu_0|dram_0 etc.
    phase            TEXT,                           -- planning|execution|synthesis|NULL
    start_ns         INTEGER NOT NULL,
    end_ns           INTEGER,
    FOREIGN KEY (span_id) REFERENCES spans(span_id)
);

CREATE INDEX IF NOT EXISTS idx_placements_span ON span_placements(span_id);

-- Explicit links between spans (not parent-child; e.g. retry links to original attempt).
CREATE TABLE IF NOT EXISTS span_links (
    link_id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    span_id          TEXT    NOT NULL,
    linked_span_id   TEXT    NOT NULL,
    link_type        TEXT    NOT NULL,               -- retry_of|recovery_of|caused_by
    FOREIGN KEY (span_id) REFERENCES spans(span_id)
);

CREATE INDEX IF NOT EXISTS idx_links_span ON span_links(span_id);

-- Timestamped events attached to a span (failures, cache hits, injection triggers).
-- attributes column is JSON validated by the owning vocabulary.
CREATE TABLE IF NOT EXISTS span_events (
    event_id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    span_id          TEXT    NOT NULL,
    event_type       TEXT    NOT NULL,
    ts_ns            INTEGER NOT NULL,               -- monotonic nanoseconds
    attributes       TEXT,                           -- JSON, vocabulary-validated
    FOREIGN KEY (span_id) REFERENCES spans(span_id)
);

CREATE INDEX IF NOT EXISTS idx_events_span ON span_events(span_id);

-- Key-value attributes set on a span before close; immutable after close (INV-10).
-- value_type tells consumers how to deserialize value_text.
CREATE TABLE IF NOT EXISTS span_attributes (
    attr_id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    span_id          TEXT    NOT NULL,
    key              TEXT    NOT NULL,
    value_text       TEXT,
    value_type       TEXT    NOT NULL DEFAULT 'string' CHECK (value_type IN ('string','int','float','bool','json')),
    UNIQUE (span_id, key),
    FOREIGN KEY (span_id) REFERENCES spans(span_id)
);

CREATE INDEX IF NOT EXISTS idx_attrs_span ON span_attributes(span_id);

-- Versioned annotations added after span close (quality scores, token counts from logs).
-- payload is JSON; annotation_type owner declares its schema.
-- Never modifies the span itself (INV-19).
CREATE TABLE IF NOT EXISTS span_annotations (
    annotation_id    INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    span_id          TEXT    NOT NULL,
    annotation_type  TEXT    NOT NULL,               -- e.g. quality_score|token_count
    annotation_version TEXT  NOT NULL DEFAULT '1',
    source           TEXT    NOT NULL,               -- plugin id or 'runner'
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload          TEXT    NOT NULL,               -- JSON
    provenance_ref   TEXT,
    FOREIGN KEY (span_id) REFERENCES spans(span_id)
);

CREATE INDEX IF NOT EXISTS idx_annotations_span ON span_annotations(span_id);

INSERT INTO schema_version (version, applied_at, description)
VALUES (113, datetime('now'), 'Span storage tables: spans, span_placements, span_links, span_events, span_attributes, span_annotations');
