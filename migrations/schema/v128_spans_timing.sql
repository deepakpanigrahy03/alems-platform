-- =============================================================================
-- v128_spans_timing.sql
-- Spans record how their times were obtained (45.8).
-- =============================================================================
-- start_ns and end_ns are wall clock epoch ns (the clock of runs and samples).
-- start_mono_ns, end_mono_ns: monotonic ns, only when observed live (else NULL).
-- timing_quality: observed | derived | unknown (end unknown stays NULL, never "now").
-- timing_source: where the times came from (runs, orchestration_events,
--   llm_interactions:request_start_ns..last_token_time_ns, live, ...).
-- parent_source: observed | inferred | default (how the parent was chosen).
-- Historical rows keep NULL in all five (they were stamped at build time; the
-- 45.8 repair rebuilds them on a copy). Rule MSC-4: DDL only. Compare header and
-- footer with v127_experiment_type_benchmark.sql before running and follow it.
-- =============================================================================

ALTER TABLE spans ADD COLUMN start_mono_ns INTEGER;
ALTER TABLE spans ADD COLUMN end_mono_ns INTEGER;
ALTER TABLE spans ADD COLUMN timing_quality TEXT CHECK (timing_quality IN ('observed', 'derived', 'unknown'));
ALTER TABLE spans ADD COLUMN timing_source TEXT;
ALTER TABLE spans ADD COLUMN parent_source TEXT CHECK (parent_source IN ('observed', 'inferred', 'default'));
