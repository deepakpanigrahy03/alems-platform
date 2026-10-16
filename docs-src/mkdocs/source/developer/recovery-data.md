---
**Status:** CURRENT
**Platforms verified:** nvidia_grace
**Last updated:** 2026-10-15
---

# Recovery Data

Scheduled recovery studies write three data streams beside the usual goal, attempt and run rows. All are written after the run row exists, never inside a measurement window, and none enters energy attribution.

## Prefix Cache Reuse Per LLM Call

Table `state_reuse_events`, one row per LLM call (one per `llm_interactions` row of the run), from the usage the serving engine returns.

| Column | Meaning |
|---|---|
| `attempt_id`, `run_id` | the attempt and its run |
| `reuse_type` | `prefix_cache` |
| `reuse_source` | `vllm` |
| `tokens_reused` | prompt tokens served from the prefix cache (`usage.prompt_tokens_details.cached_tokens`) |
| `tokens_recomputed` | `prompt_tokens - tokens_reused` |
| `reuse_fraction` | `tokens_reused / prompt_tokens` |
| `cache_hit` | 1 when `tokens_reused > 0`, else 0 |

When the engine does not report cached tokens, the four count columns are NULL, never 0.

Rows per attempt against LLM interactions (the two counts are equal):

```sql
SELECT ga.goal_id, ga.attempt_id, ga.run_id,
  (SELECT COUNT(*) FROM llm_interactions li WHERE li.run_id = ga.run_id) AS llm_rows,
  (SELECT COUNT(*) FROM state_reuse_events s WHERE s.run_id = ga.run_id) AS reuse_rows,
  (SELECT SUM(cache_hit) FROM state_reuse_events s WHERE s.run_id = ga.run_id) AS hits
FROM goal_attempt ga WHERE ga.goal_id >= <first goal> ORDER BY ga.goal_id, ga.attempt_id;
```

## Server Cache Statistics

Table `cache_state_snapshots`, one row per reading of the serving engine's `/metrics`: before every attempt (before any cache reset, so it shows the cache as the previous attempt left it) and once after the goal. The reading after attempt k is the reading before attempt k+1.

| Column | Meaning |
|---|---|
| `attempt_id`, `run_id` | the attempt the reading precedes; the final reading carries the last attempt |
| `timestamp_ns` | wall clock of the reading |
| `engine_name`, `cache_type` | `vllm`, `prefix_cache` |
| `occupancy_fraction` | `vllm:kv_cache_usage_perc`; reads 0.0 between attempts (see Known Limitations) |
| `hit_rate_aggregate` | `prefix_cache_hits_total / prefix_cache_queries_total`, cumulative since server start |

The raw counters of every reading are kept in the study ledger (`prepare[<attempt>].cache_metrics`, `cache_metrics_after`), so hit rates over a single attempt are exact differences of two readings. A failed reading writes nothing.

## Context Checkpoints

Span annotation `raf.context_checkpoint`, version 1, one per attempt, linked to the attempt span (`goal_attempt.span_id`). Payload:

```json
{"attempt": 2, "checkpoints": [
  {"turn": 2, "message_index": 31, "prompt_token_offset": 9120, "state_hash": "..."}]}
```

| Field | Meaning |
|---|---|
| `turn` | BFCL turn index, 0 based, one record per turn executed in the attempt |
| `message_index` | length of the message history at the end of the turn |
| `prompt_token_offset` | prompt tokens of the last LLM call made so far in the attempt |
| `state_hash` | public tool state hash from the per turn check; NULL when checks are off |

A first attempt that succeeds has one record per task turn. An attempt cut by a failure stops at the failing turn; a resumed or rolled back attempt starts at the turn it resumes.

```sql
SELECT ga.goal_id, ga.attempt_id,
  (SELECT json_array_length(json_extract(a.payload,'$.checkpoints')) FROM span_annotations a
     WHERE a.span_id = ga.span_id AND a.annotation_type = 'raf.context_checkpoint') AS ckpts
FROM goal_attempt ga WHERE ga.goal_id >= <first goal> ORDER BY ga.goal_id, ga.attempt_id;
```

## Validity Codes

Written to the study ledger by the `bfcl_raf` classifier. A goal with any code is rerun once.

| Code | Meaning |
|---|---|
| `INJECTION_MISMATCH` | the planned injection did not fire as planned |
| `INJECTION_NOT_REACHED` | the trajectory ended before the injection point |
| `PRE_INJECTION_FAILURE` | attempt 1 failed before the injection point |
| `CORRUPTION_NOT_APPLIED` | a contaminating class fired but changed nothing |
| `DETECTION_MISSED` | a contaminating fault was not detected by the turn check |
| `FALSE_DETECTION` | a turn check failed without an injected fault |
| `B0_FAILED` | a B0 goal had no successful attempt |
| `CACHE_RESET_FAILED`, `RESTORE_MISMATCH` | the pre attempt hook could not prepare the attempt |

`RB_NOT_APPLICABLE` is reported in `detail.rb_not_applicable` and never invalidates a goal: roll back at turn 0 equals restart plus the notice.

`detail.self_corrected` marks a goal whose injected failure fired once in attempt 1 and which still ended attempt 1 in the correct state, with no recovery attempt: every turn check passed (contaminating classes) or the BFCL checker passed (other classes). The goal is valid and never rerun. It is excluded from the comparison of recovery options and counted separately per class, option and point: the rate at which the agent recovers on its own, the baseline every recovery policy must beat.

## Known Limitations

`occupancy_fraction` reads 0.0 at every reading: vLLM counts only KV blocks held by running requests, readings happen between attempts, and blocks retained for prefix reuse count as free. Cache state at a failure is evidenced by `tokens_reused` per call and by differences of the hit counters in the ledger.

Goals recorded before the self correction rule (Oct 15) carry `DETECTION_MISSED` plus `INJECTION_MISMATCH` for a self corrected attempt; reclassify them from the ledger with the current classifier.

`hit_rate_aggregate` is cumulative; use the ledger counters for per attempt rates.

`state_hash` is NULL in studies without turn checks, by design: hashing at turn ends would add work inside the measurement window.
