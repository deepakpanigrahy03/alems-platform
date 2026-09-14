# Quality Scoring System

---
**Platform version:** A-LEMS 1.0 (schema version 91+)
**Status:** PRODUCTION
**Last updated:** 2026-09-14
---

## Table of Contents

1. [Overview](#1-overview)
2. [How Scoring Works](#2-how-scoring-works)
3. [Task Expectation Blocks](#3-task-expectation-blocks)
4. [Auto-Detection from expected_answer](#4-auto-detection-from-expected_answer)
5. [Built-in Scorers](#5-built-in-scorers)
6. [N-Judge Reconciliation](#6-n-judge-reconciliation)
7. [Quality Tables](#7-quality-tables)
8. [Adding a New Scorer](#8-adding-a-new-scorer)
9. [Configuring Quality per Task Category](#9-configuring-quality-per-task-category)
10. [The Observer Energy Property](#10-the-observer-energy-property)
11. [Interpreting Quality Results](#11-interpreting-quality-results)
12. [Reference](#12-reference)

---

## 1. Overview

A-LEMS measures the energy cost of LLM inference and agentic orchestration.
The quality scoring system adds the other half of the measurement: was the
output correct?

Together, these two measurements enable the quality-Energy per Goal (qEpG)
metric:

```
qEpG = energy_uj / quality_score
```

A task that costs 500J and scores 1.0 has qEpG = 500J.
A task that costs 500J and scores 0.5 has qEpG = 1000J (twice the cost per unit quality).
A task that fails completely (score = 0.0) has undefined qEpG.

This makes it possible to compare not just how much energy two approaches use,
but how efficiently they convert energy into correct answers.

---

## 2. How Scoring Works

Scoring runs after every experiment run, after the core energy measurement
has been committed to the database.

```
Experiment runs
      ↓
Core commits energy_uj to runs table
      ↓
save_pair() / save_single() calls _run_quality_scoring()
      ↓
_run_quality_scoring():
    1. Read task expectation from task_meta
    2. Resolve expected value (from tasks.yaml or benchmark dataset)
    3. Read actual LLM response from result["execution"]["response"]
    4. Dispatch to correct scorer via ScorerRegistry
    5. Write score to output_quality
    6. Write evidence to output_quality_judges
    7. Update goal_attempt.normalized_score and pass_fail
    8. Detect hallucination if pass_fail = 0
```

The scoring call's own energy is tracked separately in `scoring_energy_uj`
on the ScoreResult. It is never added to the task's measured energy.
This is the Observer Energy property — see Section 10.

---

## 3. Task Expectation Blocks

Every task in `config/tasks.yaml` can declare an `expectation:` block that
specifies how quality is evaluated.

The design uses two orthogonal axes:

**Axis 1: `scorer_type`** — HOW correctness is judged

**Axis 2: `expected_source`** — WHERE the expected value comes from

```yaml
- id: my_task
  category: reasoning
  prompt: "..."
  expectation:
    scorer_type: numeric        # how to judge
    expected_source: inline     # where expected value comes from
    answer: "42"                # the expected value (for inline source)
```

### scorer_type values

| Value | Use when | Expected value format |
|---|---|---|
| `exact` | Answer is a fixed string | String |
| `numeric` | Answer is a number (with tolerance) | Number string |
| `semantic` | Answer is prose (similarity-based) | Reference text |
| `rubric` | Answer needs LLM judgment | Rubric dict |
| `structural` | Correctness = tool execution, not text | Conditions list |
| `none` | No quality scoring for this task | — |

### expected_source values

| Value | Status | Description |
|---|---|---|
| `inline` | Available | Expected value in tasks.yaml |
| `benchmark_dataset` | Planned | Load from external dataset file |
| `computed_at_runtime` | Planned | Oracle query against live data |

### Complete examples

```yaml
# Exact match — fixed string answer
- id: factual_qa
  category: qa
  prompt: "Who was the first US president?"
  expectation:
    scorer_type: exact
    expected_source: inline
    answer: "George Washington"

# Numeric — math with tolerance
- id: gsm8k_basic
  category: reasoning
  prompt: "John has 5 apples and buys 7 more. How many?"
  expectation:
    scorer_type: numeric
    expected_source: inline
    answer: "12"

# Rubric — LLM judge with criteria
- id: research_summary
  category: summarization
  prompt: "Summarize the energy overhead of agentic AI..."
  expectation:
    scorer_type: rubric
    expected_source: inline
    rubric:
      key_concepts: ["orchestration", "energy", "overhead", "tool calls"]
      min_words: 80
      max_words: 200

# Structural — tool execution verification
- id: file_write_task
  category: orchestration
  prompt: "Write the experiment results to output.txt"
  expectation:
    scorer_type: structural
    expected_source: inline
    conditions:
      - tool_called: file_processor
      - file_exists: output.txt

# Semantic — prose similarity
- id: translation_task
  category: translation
  prompt: "Translate to Hindi: Agentic AI uses more energy."
  expectation:
    scorer_type: semantic
    expected_source: inline
    answer: "एजेंटिक एआई अधिक ऊर्जा का उपयोग करता है।"
```

---

## 4. Auto-Detection from expected_answer

Tasks that have `expected_answer:` at the top level but no `expectation:` block
are automatically scored using these rules:

```
expected_answer contains a digit  → numeric scorer
expected_answer is 5 words or fewer → exact scorer
otherwise                          → semantic scorer
```

This means all 42 existing tasks with `expected_answer` receive quality scoring
without any YAML changes. You can always override auto-detection by adding an
explicit `expectation:` block.

```yaml
# Auto-detected as numeric (contains "0.778")
- id: tg_single_calc
  expected_answer: "0.778 or 77.8%"
  # scorer_type: numeric inferred automatically

# Override auto-detection explicitly
- id: tg_single_calc
  expected_answer: "0.778 or 77.8%"
  expectation:
    scorer_type: numeric
    expected_source: inline
    answer: "77.8"   # more precise than the prose expected_answer
```

---

## 5. Built-in Scorers

### ExactMatchScorer (`exact`)

Compares actual output to expected answer after normalization:
strip whitespace, lowercase, collapse internal spaces.

```
actual: "  George Washington  "
expected: "george washington"
→ score: 1.0, confidence: 1.0, reason: "exact match"

actual: "Abraham Lincoln"
expected: "george washington"
→ score: 0.0, confidence: 1.0, reason: "no match: actual='abraham lincoln' expected='george washington'"
```

Confidence is always 1.0 — exact match is deterministic.

### NumericScorer (`numeric`)

Extracts the first number from actual and expected, then compares with
1% relative tolerance.

```
actual: "John now has 12 apples."   → extracted: 12.0
expected: "12"                      → extracted: 12.0
→ score: 1.0, confidence: 1.0, reason: "numeric match: 12.0 ≈ 12.0"

actual: "The answer is 77.78%"      → extracted: 77.78
expected: "77.8"                    → extracted: 77.8
relative diff = 0.02/77.8 = 0.026%  → within 1% tolerance
→ score: 1.0, confidence: 1.0, reason: "numeric match: 77.78 ≈ 77.8"
```

Handles: integers, floats, comma-formatted numbers (1,234), percentages,
and simple word numbers (twelve → 12).

### SemanticScorer (`semantic`)

Computes semantic similarity between actual and expected.

**With sentence-transformers installed:**
Uses `all-MiniLM-L6-v2` model to compute cosine similarity between
sentence embeddings. Confidence: 0.85.

**Without sentence-transformers:**
Falls back to Jaccard token overlap ratio. Confidence: 0.60.

```bash
# Install for higher accuracy semantic scoring
pip install sentence-transformers
```

### LLMJudgeScorer (`rubric`)

Calls an LLM to score the actual output against the expected answer or rubric.
Returns score, confidence, and reasoning from the model's JSON response.

The judge model is configured per task category in `task_quality_config`:

```yaml
# config/app_settings.yaml
plugins:
  output_quality:
    judge_model: "llama3.1:8b"
    judge_temperature: 0.0
```

The judge call uses the VLLM remote URL if `ALEMS_VLLM_REMOTE_URL` is set,
otherwise falls back to local Ollama at `localhost:11434`.

### StructuralScorer (`structural`)

Verifies agentic execution behavior from the execution trace.
Does not look at the LLM's text output at all — correctness is defined
by what tools were called.

Supported conditions:

| Condition | Passes when |
|---|---|
| `tool_called: <name>` | At least one call to this tool was made |
| `tool_succeeded: <name>` | At least one call to this tool succeeded |
| `api_called: true` | At least one `api_query` or `web_search` call was made |
| `file_exists: <path>` | A file at this path was written |
| `min_tool_calls: <n>` | At least N total tool calls were made |
| `all_steps_succeeded: true` | All steps in the execution trace succeeded |

Score = fraction of conditions that passed.

```yaml
# All three conditions must pass for score = 1.0
conditions:
  - tool_called: database_query    # ← 1/3
  - tool_succeeded: database_query # ← 2/3
  - min_tool_calls: 2              # ← 3/3
```

---

## 6. N-Judge Reconciliation

When `n_judges > 1` in `task_quality_config`, multiple scorer calls are made
and their scores are reconciled using median-based rules.

```
Scores: [0.8, 0.85]
Median: 0.825
Max deviation from median: 0.025
→ within TIGHT_BAND (0.20) → method: "averaged", score: 0.825

Scores: [0.5, 0.9]
Median: 0.7
Max deviation: 0.2 (= TIGHT_BAND exactly, fails tight check)
→ within LOOSE_BAND (0.40) → method: "consensus_median", score: 0.7

Scores: [0.8, 0.82, 0.1]
One outlier (0.1 deviates 0.61 from median 0.71)
→ method: "majority_median", score: median([0.8, 0.82]) = 0.81

Scores: [0.05, 0.95]
Both deviate 0.45 from median — outside LOOSE_BAND
→ method: "needs_review", score: NULL
```

`needs_review` means the judges disagreed too strongly to produce a reliable
score. The attempt is not counted as pass or fail until a human reviews it.

### Configuring N-judges per task category

```sql
-- Set 2 judges for reasoning tasks
UPDATE task_quality_config
SET n_judges = 2
WHERE task_category = 'reasoning';

-- Set different judge models per call
UPDATE task_quality_config
SET judge_model_set = '["llama3.1:8b", "mistral-7b"]'
WHERE task_category = 'summarization';
```

---

## 7. Quality Tables

### output_quality

One row per attempt per run. Primary quality record.

| Column | Type | Description |
|---|---|---|
| `quality_id` | INTEGER | Primary key |
| `attempt_id` | INTEGER | FK to goal_attempt |
| `goal_id` | INTEGER | FK to goal_execution |
| `task_category` | TEXT | Task category from task_meta |
| `metric_type` | TEXT | binary, scalar, pairwise, testsuite |
| `raw_score` | REAL | Mean of valid judge scores |
| `normalized_score` | REAL | Final score [0.0, 1.0] |
| `pass_fail` | INTEGER | 1=pass, 0=fail, NULL=needs_review |
| `judge_method` | TEXT | Which scorer ran |
| `judge_count` | INTEGER | How many judges produced valid scores |
| `agreement_score` | REAL | 1 - stdev of judge scores |
| `score_method` | TEXT | How scores were reconciled |
| `expected_output` | TEXT | Expected answer (truncated to 500 chars) |
| `actual_output` | TEXT | LLM response (truncated to 500 chars) |
| `energy_uj_at_judgment` | INTEGER | Run energy at time of scoring |
| `judged_at` | TIMESTAMP | When scoring ran |

### output_quality_judges

One row per judge call. Evidence trail.

| Column | Type | Description |
|---|---|---|
| `judge_entry_id` | INTEGER | Primary key |
| `quality_id` | INTEGER | FK to output_quality |
| `judge_model` | TEXT | Model or scorer type used |
| `judge_score` | REAL | Raw score from this judge |
| `judge_confidence` | REAL | Confidence from this judge |
| `judge_reasoning` | TEXT | Human-readable explanation |

### task_quality_config

Configuration per task category. Seeded by `scripts/seed_quality_config.py`.

| Column | Type | Description |
|---|---|---|
| `task_category` | TEXT | Primary key (matches tasks.yaml category) |
| `metric_type` | TEXT | binary, scalar, pairwise, testsuite |
| `judge_method` | TEXT | Default scorer for this category |
| `threshold` | REAL | Minimum score for pass_fail=1 |
| `n_judges` | INTEGER | Number of judge calls per attempt |
| `judge_model_set` | TEXT | JSON array of judge model IDs |

---

## 8. Adding a New Scorer

Creating a new scorer requires three files and one bootstrap line.

### Step 1: Implement ScorerABC

```python
# core/execution/scorers/my_scorer.py

from typing import Optional, Tuple
from core.execution.scorers.abc import ScorerABC
from core.execution.scorers.context import TaskExecutionContext, ScoreResult

class MyScorer(ScorerABC):
    SCORER_TYPE = "my_scorer"      # must be unique
    METRIC_TYPES = ("scalar",)     # which metric_types this handles

    def score(
        self,
        actual: str,
        expected: str,
        judge_model: Optional[str] = None,
        rubric: Optional[dict] = None,
    ) -> Tuple[float, float, str]:
        """
        Score actual against expected.
        Returns (score, confidence, reasoning).
        Must never raise — return (0.0, 0.0, 'scorer_failed') on error.
        """
        try:
            # Your scoring logic here
            score = 1.0 if actual.strip() == expected.strip() else 0.0
            return (score, 1.0, f"my scorer: score={score}")
        except Exception as exc:
            return (0.0, 0.0, "scorer_failed")

    def score_with_context(self, context: TaskExecutionContext) -> ScoreResult:
        """
        Override this if your scorer needs the full execution trace.
        Default calls score(actual, expected) — fine for most scorers.
        """
        score, conf, reason = self.score(
            actual=context.model_output,
            expected=str(context.expected),
        )
        return ScoreResult(score=score, confidence=conf, reasoning=reason)
```

### Step 2: Register in bootstrap.py

```python
# core/execution/scorers/bootstrap.py — add inside register_all_scorers()

    try:
        from core.execution.scorers.my_scorer import MyScorer
        scorer_registry.register(MyScorer)
    except Exception as exc:
        logger.error("scorer_bootstrap: failed to register MyScorer: %s", exc)
```

### Step 3: Add to judge_method map in experiment_runner.py

```python
# In _run_quality_scoring(), update _judge_method_map:
_judge_method_map = {
    "numeric":    "exact_match",
    "exact":      "exact_match",
    "semantic":   "semantic",
    "rubric":     "llm_judge",
    "structural": "exact_match",
    "my_scorer":  "exact_match",   # ← add your mapping
}
```

### Step 4: Use in tasks.yaml

```yaml
- id: my_task
  expectation:
    scorer_type: my_scorer
    expected_source: inline
    answer: "reference answer"
```

### Future: pip-installable scorer

In a future release, community scorers will register via Python entry points:

```toml
# pyproject.toml
[project.entry-points."alems.scorers"]
my_scorer = "my_package.my_scorer:MyScorer"
```

After `pip install my-alems-scorer`, the scorer is discovered automatically.
No changes to bootstrap.py or experiment_runner.py are needed.

---

## 9. Configuring Quality per Task Category

The `task_quality_config` table controls quality settings per category.
Seed it with `scripts/seed_quality_config.py`.

### Check current configuration

```bash
sqlite3 $ALEMS_DB "
SELECT task_category, metric_type, judge_method, threshold, n_judges
FROM task_quality_config
ORDER BY task_category;"
```

### Update threshold for a category

```bash
sqlite3 $ALEMS_DB "
UPDATE task_quality_config
SET threshold = 0.9
WHERE task_category = 'coding';"
```

### Add a new category

```bash
sqlite3 $ALEMS_DB "
INSERT INTO task_quality_config
    (task_category, metric_type, judge_method, threshold, n_judges)
VALUES
    ('my_category', 'scalar', 'llm_judge', 0.75, 2);"
```

### Re-seed from scratch

```bash
# Wipe and re-seed all categories
sqlite3 $ALEMS_DB "DELETE FROM task_quality_config;"
python3 scripts/seed_quality_config.py --verify
```

---

## 10. The Observer Energy Property

The quality scorer runs after core energy is committed.
This is not a convention — it is enforced by call order in the platform.

```
save_pair():
    1. Core commits energy_uj   ← energy measured here
    2. ETL runs
    3. _run_quality_scoring()   ← scorer runs here, AFTER energy committed
```

The scorer's own energy consumption (especially LLM judge calls) does not
contaminate the task's measured energy. This is the Observer Energy property:
the act of measuring quality does not change what was measured.

This matters for research validity. If quality scoring energy were mixed into
the task energy, then:

- A task scored by an exact match (microseconds) would appear more efficient
  than the same task scored by an LLM judge (seconds of inference)
- Comparing qEpG across scorer types would be meaningless

The `scoring_energy_uj` field on `ScoreResult` tracks how much energy the
scoring call itself consumed. In a future release, this will be written to
a separate `scoring_overhead` table and explicitly excluded from all qEpG
computations.

---

## 11. Interpreting Quality Results

### Basic quality check for an experiment

```sql
SELECT
    oq.task_category,
    r.workflow_type,
    AVG(oq.normalized_score) as avg_quality,
    SUM(oq.pass_fail) as passes,
    COUNT(*) as total
FROM output_quality oq
JOIN goal_attempt ga ON ga.attempt_id = oq.attempt_id
JOIN runs r ON r.run_id = ga.run_id
JOIN experiments e ON e.exp_id = r.exp_id
WHERE e.exp_id = ?
GROUP BY oq.task_category, r.workflow_type
ORDER BY oq.task_category, r.workflow_type;
```

### Quality-energy tradeoff (qEpG)

```sql
SELECT
    oq.task_category,
    r.workflow_type,
    AVG(r.total_energy_uj) / 1e6 as avg_energy_j,
    AVG(oq.normalized_score) as avg_quality,
    CASE
        WHEN AVG(oq.normalized_score) > 0
        THEN AVG(r.total_energy_uj) / AVG(oq.normalized_score) / 1e6
        ELSE NULL
    END as qEpG_joules
FROM output_quality oq
JOIN goal_attempt ga ON ga.attempt_id = oq.attempt_id
JOIN runs r ON r.run_id = ga.run_id
WHERE oq.pass_fail IS NOT NULL
GROUP BY oq.task_category, r.workflow_type
ORDER BY qEpG_joules;
```

### Hallucination rate by category

```sql
SELECT
    oq.task_category,
    COUNT(he.hallucination_id) as hallucinations,
    COUNT(oq.quality_id) as total_attempts,
    ROUND(100.0 * COUNT(he.hallucination_id) / COUNT(oq.quality_id), 1) as rate_pct
FROM output_quality oq
LEFT JOIN hallucination_events he ON he.attempt_id = oq.attempt_id
GROUP BY oq.task_category
ORDER BY rate_pct DESC;
```

### Attempts needing human review

```sql
SELECT
    oq.quality_id, oq.attempt_id, oq.task_category,
    oq.score_method, oq.judge_count,
    substr(oq.actual_output, 1, 100) as actual_preview
FROM output_quality oq
WHERE oq.score_method = 'needs_review'
ORDER BY oq.judged_at DESC;
```

---

## 12. Reference

### Scorer type to judge_method mapping

The `output_quality.judge_method` column uses a constrained value set
that predates the scorer system. This table shows how scorer types map:

| scorer_type | judge_method stored in DB |
|---|---|
| `exact` | `exact_match` |
| `numeric` | `exact_match` |
| `semantic` | `semantic` |
| `rubric` | `llm_judge` |
| `structural` | `exact_match` |

### score_method values

| Value | Meaning |
|---|---|
| `single_judge` | N=1, score taken as-is |
| `averaged` | All judges within tight band, mean taken |
| `consensus_median` | All judges within loose band, median taken |
| `majority_median` | One outlier dropped, median of agreeing judges |
| `needs_review` | Judges disagreed too much, human review required |
| `conservative_min` | Reserved for future use |

### Reconciliation thresholds

| Threshold | Value | Effect |
|---|---|---|
| `MEDIAN_TIGHT_BAND` | 0.20 | All within ±0.20 → averaged |
| `MEDIAN_LOOSE_BAND` | 0.40 | All within ±0.40 → consensus_median |
| `ACCEPTANCE_THRESHOLD` | 0.70 | score >= 0.70 → pass_fail=1 |

### Files

| File | Purpose |
|---|---|
| `core/execution/scorers/` | Scorer adapter package |
| `core/execution/expectation/` | Expectation resolution package |
| `core/execution/quality_judge.py` | N-judge orchestrator |
| `core/execution/hallucination_detector.py` | Hallucination classification |
| `config/tasks.yaml` | Task definitions with expectation blocks |
| `scripts/seed_quality_config.py` | Seed task_quality_config table |
| `core/utils/provenance.py` | Quality scoring method provenance |
