# Hallucination and Output Quality Methodology
*Chunk 8.3 | Schema Revision: 028*

---

## Overview

This document covers the measurement methodology for two new research tables introduced in Chunk 8.3:

- `hallucination_events` — energy cost of incorrect outputs
- `output_quality` — reconciled judgment verdict per attempt
- `output_quality_judges` — per-judge evidence trail

These tables answer the core research question: **how much energy does the agentic workflow waste on wrong answers?**

---

## Hallucination Detection Methodology
*method_id: `hallucination_detection_v1` | confidence: 0.85*

### Definition

A hallucination is an unsupported or incorrect output later classified as hallucinatory by a detection pipeline. Confidence is not required at detection time — some pipelines have no logit access.

### Taxonomy

Hallucination types and detection methods are governed by `core/ontology_registry.py` v1.0.0. This is the single source of truth across all papers on this platform.

| Field | Governance | Extensibility |
|---|---|---|
| `hallucination_type` | `ontology_registry.HALLUCINATION_TYPES` | PR to registry — no DB migration |
| `detection_method` | `ontology_registry.DETECTION_METHODS` | PR to registry + new `method_id` in provenance |

### Detection Signals

**`detection_confidence`** — the detection pipeline's confidence (0–1) that this output is hallucinatory. This is not model logprob. It is the detector's own assessment. Nullable when detection pipeline produces binary yes/no only.

**`semantic_similarity`** — cosine similarity between embeddings of `expected_output` and `actual_output`, range 0–1. Nullable when detection method does not use embeddings.

**`severity`** — continuous 0.0–1.0 scale indicating impact magnitude. NULL in Chunk 8.3 — populated by a future chunk that defines a `hallucination_severity_vN` method.

### Nullable Trace Links

`decision_id`, `interaction_id`, and `orchestration_event_id` are all nullable. Real systems do not always have full traceability from a hallucination back to its originating event.

---

## Hallucination Wasted Energy Methodology
*method_id: `hallucination_wasted_energy_v1` | confidence: 0.85*

### Definition

$$E_{wasted} = E_{\text{attempt\_start} \to \text{detected}}$$

Energy consumed from the start of the attempt until the hallucination was detected. This is the energy that would have been saved if the hallucination had been caught earlier.

### Population

`wasted_energy_uj` is NULL at insert time. Populated asynchronously by `chunk8_attribution_etl.py` (Agent 8.4). Source: `orchestration_events.event_energy_uj` for the relevant event window.

### Corrected-Later Derivation

Whether a hallucination was subsequently corrected is **not stored** in this table. Derive when needed:

```sql
SELECT he.*,
       CASE WHEN EXISTS (
           SELECT 1 FROM goal_attempt ga2
           WHERE ga2.goal_id = he.goal_id
             AND ga2.attempt_number > ga.attempt_number
             AND ga2.outcome = 'success'
       ) THEN 1 ELSE 0 END AS corrected_by_retry
FROM hallucination_events he
JOIN goal_attempt ga ON he.attempt_id = ga.attempt_id
```

---

## Output Quality Normalization Methodology
*method_id: `output_quality_normalization_v1` | confidence: 0.88*

### Architecture

`output_quality` (parent) holds the reconciled verdict. `output_quality_judges` (child) holds one row per judge per attempt. This supports N judges without schema changes.

Since schema version 94, `output_quality` no longer enforces one row per
attempt. Multiple rows per attempt are expected and correct: a live
scoring row (`score_method` not `back_scored`) plus zero or more
retroactive back-scored rows. See Re-Judging below.

### Reconciliation: Median-Deviation Bands

Reconciliation is median-based, not a pairwise agreement formula. Let
$s_1, \ldots, s_N$ be the N judges' raw scores and $m = \mathrm{median}(s_1,\ldots,s_N)$:

$$
s_{norm} = \begin{cases}
s_1 & N = 1 \quad (\texttt{single\_judge}) \\
\bar{s} & N \geq 2,\ \max_i |s_i - m| \leq 0.20 \quad (\texttt{averaged}) \\
m & N \geq 2,\ \max_i |s_i - m| \leq 0.40 \quad (\texttt{consensus\_median}) \\
\mathrm{median}(s_{\text{agreeing}}) & N \geq 2,\ \text{exactly one } i \text{ with } |s_i - m| > 0.20 \quad (\texttt{majority\_median}) \\
\text{NULL} & \text{otherwise} \quad (\texttt{needs\_review})
\end{cases}
$$

`agreement_score` is a separate, simpler diagnostic, not part of the
reconciliation decision itself: $1 - \min(1, \mathrm{stdev}(s_1,\ldots,s_N))$
for $N \geq 2$, or $1.0$ for a single judge (trivially self-agreeing).

Confidence 0.88, not 1.0: the tight/loose band thresholds (0.20 / 0.40)
were chosen by design judgment, not calibrated against a labeled
disagreement dataset. They correctly separate the cases tested this
session (single clear outlier vs. genuine 3-way split) but have not been
validated against a larger, independently-labeled sample of judge
disagreements. To reach 1.0 would require such a calibration study.

### Judge Model Selection

Each judge call resolves its model via `task_quality_config.judge_model_set`
(a JSON array of `{provider, model_id}` pairs, resolved through
`ModelFactory.get_adapter()` — the same provider-resolution path used for
every inference call on this platform, not a scorer-private HTTP client).
Judges are assigned round-robin across the configured set: with 2 judges
and 1 configured model, the same model is called twice (two independent
completions, still two audited rows); with 2 judges and 2 configured
models, each model is called once. A category with no `judge_model_set`
configured falls back to the scorer's own default, which has no
guaranteed reachability on every platform (verified: attempting the
unconfigured default on a platform without a local Ollama installation
fails outright, since it targets a fixed local endpoint).

### Analysis Exclusion

Rows with `score_method = 'needs_review'` or `score_method = 'stub_skipped'`
are excluded from all paper analysis queries:

```sql
WHERE score_method NOT IN ('needs_review', 'stub_skipped')
```

`stub_skipped` marks a scorer that is registered but not yet implemented
(see `ScorerABC.STATUS`) — distinct from a real judgment that failed to
converge. The disagreement rate (proportion of `needs_review` rows,
excluding `stub_skipped`) should be reported separately in the paper as
a data quality metric.

### Re-Judging

`output_quality` does not enforce one row per attempt. Live scoring
inserts one row per attempt with `score_method` reflecting the real-time
reconciliation outcome. Retroactive back-scoring inserts additional rows
tagged `score_method = 'back_scored'`, never overwriting or deleting
prior rows. The current verdict for an attempt is the most recent row:

```sql
SELECT * FROM output_quality
WHERE attempt_id = ?
ORDER BY judged_at DESC, quality_id DESC
LIMIT 1;
```

### Judge Reproducibility Fields

`judge_prompt_hash`, `judge_version`, `judge_temperature`, and
`judge_provider` in `output_quality_judges` remain nullable — prompt
hashing is not yet implemented in the experiment runner. `judge_model`
is populated (the resolved `model_id`, not a provider/adapter internal
name).

### Known Limitations

- **Reconciliation bands are uncalibrated**: the 0.20/0.40 median-deviation
  thresholds are a design choice, not derived from a labeled dataset of
  judge disagreements. Workaround: report the `needs_review` rate
  alongside any quality claim so a reviewer can judge threshold
  sensitivity themselves.
- **Round-robin repetition is not independent replication**: when
  `judge_model_set` has fewer entries than `n_judges`, the same model is
  called more than once. This increases judge *count* for reconciliation
  purposes but does not increase genuine model diversity. Workaround:
  configure `judge_model_set` with as many distinct models as `n_judges`
  when independence matters for a specific paper claim.

## Provenance Summary
| Column | Method | Type |
|---|---|---|
| `he.detection_confidence` | `hallucination_detection_v1` | INFERRED |
| `he.semantic_similarity` | `hallucination_detection_v1` | INFERRED |
| `he.severity` | `hallucination_detection_v1` | INFERRED |
| `he.wasted_energy_uj` | `hallucination_wasted_energy_v1` | CALCULATED |
| `oq.raw_score` | `output_quality_normalization_v1` | MEASURED |
| `oq.normalized_score` | `output_quality_normalization_v1` | CALCULATED |
| `oq.agreement_score` | `output_quality_normalization_v1` | CALCULATED |
| `oq.energy_uj_at_judgment` | `goal_execution_rollup_v1` | CALCULATED |
| `oqj.judge_score` | `output_quality_normalization_v1` | MEASURED |
| `oqj.judge_confidence` | `output_quality_normalization_v1` | INFERRED |

---

## What Agent 8.3 Does NOT Do

- Does not write ETL to populate `wasted_energy_uj` — owned by Agent 8.4
- Does not backfill `normalization_factors` — owned by Agent 8.4
- Does not populate `severity` — owned by a future chunk
- Does not touch `run_quality`, `goal_execution`, `goal_attempt` schema
