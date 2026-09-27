---
**Schema versions:** v113 (span tables), v114 (span_id columns), v115 (attribution metadata)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64)
**Status:** PRODUCTION
**Last updated:** 2026-09-27
---

# A-LEMS Agent Span Vocabulary

## Overview

A-LEMS records every experiment run as a span tree: a hierarchy of timed,
attributed records that capture what happened during execution.
Spans connect energy measurements to the semantic structure of agentic work.
Without spans, you know a run used 49mJ.
With spans, you know that 42mJ went to the LLM call, 4mJ to tool execution,
and 3mJ to orchestration overhead.

The agent vocabulary defines span kinds for agentic and linear workflows.
It is the first-party semantic layer built on the A-LEMS span contract
(DESIGN_CHUNK39_v4 section 8).

## The Span Tree

Every run produces one trace.
A save_pair run produces two traces sharing one trace_id: linear and agentic.
Each trace has this structure:

```
run
 └── goal
      └── attempt
           ├── phase:planning
           ├── tool_call
           ├── phase:execution
           ├── phase:synthesis
           └── llm_call
```

Linear runs omit phases and tool_calls (no orchestration layer).

## Span Kinds

| Kind | What it represents | Key attributes |
|---|---|---|
| run | Root span, one per run_id | workflow_type, task_id |
| goal | The research task being solved | task_id, workflow_type, attributed_energy_uj, outcome |
| attempt | One execution attempt (multiple on retry) | attempt_number, is_retry, outcome |
| phase | planning / execution / synthesis phase | phase, duration_ns, event_energy_uj |
| tool_call | One tool invocation | tool_name, tool_success, duration_ns |
| llm_call | One LLM API call | model_name, prompt_tokens, completion_tokens, ttft_ms, tpot_ms, api_latency_ms, prefill_energy_uj |

## Span Events

Events capture things that happened inside a span:

| Event type | Fires when | Key attributes |
|---|---|---|
| tool_error | Tool invocation failed | tool_name, error message |
| tool_slow | Tool took more than 5 seconds | duration_ns, tool_name |
| high_ttft | Time to first token exceeded 2000ms | ttft_ms |
| low_throughput | Time per output token exceeded 200ms | tpot_ms |
| api_error | LLM API returned an error | error message |
| tcp_retransmit | Network retransmits detected | tcp_retransmits |
| retry | Attempt retried after failure | attempt_number, failure_cause |

## Span Annotations

Annotations are post-hoc, versioned records attached to closed spans.
The quality judge writes a quality_score annotation on every attempt span
after scoring completes.

| Annotation type | Source | Payload |
|---|---|---|
| quality_score | quality_judge | normalized_score, pass_fail, attempt_number, is_retry |

## Device Placements

Placements record which hardware device executed which phase:

| Span kind | Device | Phase |
|---|---|---|
| llm_call | cpu_package | prefill |
| llm_call | gpu_0 | decode (when GPU detected) |
| phase:planning | cpu_package | planning |
| phase:execution | cpu_package | execution |
| phase:execution | gpu_0 | execution (when GPU detected) |
| phase:synthesis | cpu_package | synthesis |
| tool_call | cpu_package | execution |

## Platform Coverage

| Platform | Architecture | Span support | GPU placement | Status |
|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | full | yes (gpu_0) | VERIFIED |
| Intel i7-1165G7 | x86_64 | full | yes (gpu_0) | VERIFIED |
| Apple M1 Pro | arm64 | full | no (IOKit, not wired) | PLANNED |
| AMD Ryzen | x86_64 | full | yes (gpu_0) | PENDING |

## Query Reference

All queries assume SQLite. Replace run_id, exp_id values as needed.

### 1. Full span tree for one run

```sql
SELECT s.run_id, s.span_id, s.kind, s.name, s.status,
       s.start_ns, s.end_ns
FROM spans s
WHERE s.run_id = <run_id>
ORDER BY s.start_ns;
```

### 2. Energy per span kind for one experiment

```sql
SELECT s.kind,
       AVG(CAST(sa.value_text AS REAL)) AS avg_energy_uj,
       COUNT(*) AS span_count
FROM span_attributes sa
JOIN spans s ON s.span_id = sa.span_id
JOIN runs r ON r.run_id = s.run_id
WHERE r.exp_id = <exp_id>
  AND sa.key = 'attributed_energy_uj'
GROUP BY s.kind
ORDER BY avg_energy_uj DESC;
```

### 3. LLM call latency across an experiment

```sql
SELECT r.run_id, r.workflow_type,
       CAST(ttft.value_text AS REAL) AS ttft_ms,
       CAST(tpot.value_text AS REAL) AS tpot_ms,
       CAST(tokens.value_text AS INTEGER) AS completion_tokens
FROM spans s
JOIN runs r ON r.run_id = s.run_id
LEFT JOIN span_attributes ttft  ON ttft.span_id  = s.span_id AND ttft.key  = 'ttft_ms'
LEFT JOIN span_attributes tpot  ON tpot.span_id  = s.span_id AND tpot.key  = 'tpot_ms'
LEFT JOIN span_attributes tokens ON tokens.span_id = s.span_id AND tokens.key = 'completion_tokens'
WHERE r.exp_id = <exp_id>
  AND s.kind = 'llm_call'
ORDER BY r.run_id;
```

### 4. Quality score per attempt

```sql
SELECT r.run_id, r.workflow_type,
       sn.payload
FROM span_annotations sn
JOIN spans s ON s.span_id = sn.span_id
JOIN runs r ON r.run_id = s.run_id
WHERE r.exp_id = <exp_id>
  AND sn.annotation_type = 'quality_score'
ORDER BY r.run_id;
```

### 5. Energy per correct answer (paper unit)

```sql
SELECT r.workflow_type,
       AVG(CAST(energy.value_text AS REAL)) AS avg_energy_uj,
       AVG(CAST(json_extract(sn.payload, '$.normalized_score') AS REAL)) AS avg_quality,
       SUM(CAST(json_extract(sn.payload, '$.pass_fail') AS INTEGER)) AS correct_count,
       COUNT(*) AS total_runs
FROM span_annotations sn
JOIN spans s ON s.span_id = sn.span_id
JOIN runs r ON r.run_id = s.run_id
JOIN spans goal_s ON goal_s.run_id = r.run_id AND goal_s.kind = 'goal'
JOIN span_attributes energy ON energy.span_id = goal_s.span_id
  AND energy.key = 'attributed_energy_uj'
WHERE r.exp_id = <exp_id>
  AND sn.annotation_type = 'quality_score'
GROUP BY r.workflow_type;
```

### 6. Tool call success rate per experiment

```sql
SELECT CAST(success.value_text AS INTEGER) AS tool_success,
       COUNT(*) AS count
FROM spans s
JOIN runs r ON r.run_id = s.run_id
JOIN span_attributes success ON success.span_id = s.span_id
  AND success.key = 'tool_success'
WHERE r.exp_id = <exp_id>
  AND s.kind = 'tool_call'
GROUP BY tool_success;
```

### 7. Runs with tool errors

```sql
SELECT r.run_id, r.workflow_type,
       se.event_type, se.attributes, se.ts_ns
FROM span_events se
JOIN spans s ON s.span_id = se.span_id
JOIN runs r ON r.run_id = s.run_id
WHERE r.exp_id = <exp_id>
  AND se.event_type = 'tool_error'
ORDER BY r.run_id;
```

### 8. Retry analysis

```sql
SELECT r.run_id,
       CAST(attempt_num.value_text AS INTEGER) AS attempt_number,
       CAST(is_retry.value_text AS TEXT) AS is_retry,
       outcome.value_text AS outcome,
       json_extract(sn.payload, '$.normalized_score') AS quality_score
FROM spans s
JOIN runs r ON r.run_id = s.run_id
JOIN span_attributes attempt_num ON attempt_num.span_id = s.span_id
  AND attempt_num.key = 'attempt_number'
JOIN span_attributes is_retry ON is_retry.span_id = s.span_id
  AND is_retry.key = 'is_retry'
JOIN span_attributes outcome ON outcome.span_id = s.span_id
  AND outcome.key = 'outcome'
LEFT JOIN span_annotations sn ON sn.span_id = s.span_id
  AND sn.annotation_type = 'quality_score'
WHERE r.exp_id = <exp_id>
  AND s.kind = 'attempt'
ORDER BY r.run_id;
```

### 9. Conservation residual check

```sql
SELECT r.run_id, r.workflow_type,
       ar.domain, ar.measured_uj, ar.attributed_uj,
       ar.residual_uj, ar.status
FROM attribution_residual ar
JOIN runs r ON r.run_id = ar.run_id
WHERE r.exp_id = <exp_id>
ORDER BY r.run_id, ar.domain;
```

### 10. Phase energy breakdown (agentic only)

```sql
SELECT r.run_id,
       CAST(phase.value_text AS TEXT) AS phase_name,
       CAST(energy.value_text AS REAL) AS event_energy_uj,
       CAST(dur.value_text AS INTEGER) AS duration_ns
FROM spans s
JOIN runs r ON r.run_id = s.run_id
LEFT JOIN span_attributes phase  ON phase.span_id  = s.span_id AND phase.key  = 'phase'
LEFT JOIN span_attributes energy ON energy.span_id = s.span_id AND energy.key = 'event_energy_uj'
LEFT JOIN span_attributes dur    ON dur.span_id    = s.span_id AND dur.key    = 'duration_ns'
WHERE r.exp_id = <exp_id>
  AND s.kind = 'phase'
ORDER BY r.run_id, s.start_ns;
```

## Verification

After any experiment run, verify spans are populated:

```bash
DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools'); \
from path_loader import get_alems_db_path; print(get_alems_db_path())")

sqlite3 "$DB" "
SELECT 'spans',              count(*) FROM spans
UNION ALL SELECT 'span_placements',  count(*) FROM span_placements
UNION ALL SELECT 'span_attributes',  count(*) FROM span_attributes
UNION ALL SELECT 'span_events',      count(*) FROM span_events
UNION ALL SELECT 'span_annotations', count(*) FROM span_annotations
UNION ALL SELECT 'attribution_residual', count(*) FROM attribution_residual;
"
```

Expected for a clean save_pair run: spans > 0, placements > 0,
attributes > 0, annotations > 0, residual = 10 rows (5 per run side).
span_events = 0 on clean runs, > 0 on injection or retry runs.

## Known Limitations

- **span_links**: Not populated in foundation phases.
  Cross-trace linking requires the observer daemon (39.7).

- **span_annotations other than quality_score**: No other annotation types
  declared in foundation. OTel normalization annotations arrive in 39.6.

- **GPU placement**: Requires GPU energy detected in ml_features.
  On CPU-only runs, gpu_0 placement is absent from llm_call spans.

- **tool_name on tool_call spans**: Shows as "unknown" when the executor
  does not populate the tool_name key in orchestration_events metadata.
  Tracked as backlog item.

- **dram and uncore domains in attribution_residual**: Show as not_applicable
  on platforms where DRAM energy is not measured (llama_cpp local, macOS).

- **outcome on agentic goal span**: Set to "unknown" when execution status
  is not propagated through the result dict from the executor.
  Backfilled from goal_attempt after _record_goal_pair in save_pair and
  save_single paths.
