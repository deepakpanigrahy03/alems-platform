# A Researcher's Journey

This guide walks through the complete workflow from research question
to a clean, citable dataset. A-LEMS is designed around this journey:
every design decision from the three-scope configuration system to the
provenance chain exists to make this workflow reproducible, auditable,
and paper-ready.

---

## Stage 1: Define Your Research Question

A-LEMS measures energy at the hardware counter level. Every experiment
you run answers a specific, measurable question. Before configuring
anything, state your question precisely enough that you can express it
as an experiment goal string.

Examples of well-formed research questions for A-LEMS:

- What is the orchestration tax of a 3-tool agentic workflow versus a
  single linear call on NVIDIA Grace at 70B parameter scale?
- How does energy per token vary across Groq, vllm_remote, and
  llama_cpp for arithmetic reasoning tasks?
- What fraction of agentic energy is consumed by tool execution versus
  synthesis across difficulty levels 1, 2, and 3?

The `--experiment-goal` argument captures this question in the database
alongside every run. It is not metadata — it is a searchable field that
lets you reconstruct the intent of any experiment months later.

---

## Stage 2: Choose Your Platform and Provider

Check what is available on your machine:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

# Platform and measurement tier
bash scripts/alems dev status

# Available providers
python3 core/execution/tests/test_llm_setup.py --provider all

# Available tasks
python3 core/execution/tests/run_experiment.py --list-tasks
```

**Platform matters for methodology.** A result from `nvidia_grace`
with `energy_measurement: direct` (SPBM) is not directly comparable
to a result from `linux_arm` with `energy_measurement: modeled`
(energy_uj = 0). Your paper methodology section must state the
measurement tier for every platform used.

See [Platform Matrix](../reference/platform-matrix.md) for the full
energy stack per platform and [Energy Tiers](../concepts/energy-tiers.md)
for what each tier means scientifically.

---

## Stage 3: Run a Pilot

Before committing to a full experiment, run one repetition in debug
mode to confirm the setup works end to end:

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 1 \
  --provider groq \
  --workflow-mode comparison \
  --experiment-type pilot \
  --experiment-goal "pilot: verify setup before full run" \
  --verbose
```

`--verbose` prints hardware counter values per repetition. Check:

- `total_energy_uj` is non-zero on direct energy platforms
- `energy_sample_coverage_pct` is above 80%
- Both linear and agentic runs complete without error
- Provider latency is within expected range

If the pilot passes, proceed to the full experiment. If not, see
[Troubleshooting](../getting-started/troubleshooting.md).

---

## Stage 4: Design the Experiment

A well-designed A-LEMS experiment varies one thing at a time. Use the
`experiment_type` field to declare your research intent:

| Intent | experiment_type |
|---|---|
| Standard measurement | `normal` |
| Measuring orchestration or framework overhead | `overhead_study` |
| Studying retry behavior and failure energy | `retry_study` |
| Controlled tool failure injection | `failure_injection` |
| Varying quality parameters across runs | `quality_sweep` |
| Systematic feature removal | `ablation` |
| Hardware calibration | `calibration` |

Group related runs under the same experiment goal string. This makes
them queryable as a cohort:

```sql
SELECT r.run_id, r.workflow_type, e.experiment_goal, e.experiment_type
FROM runs r
JOIN experiments e ON r.exp_id = e.exp_id
WHERE e.experiment_goal LIKE '%your research question%'
ORDER BY r.run_id;
```

---

## Stage 5: Run the Full Experiment

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 10 \
  --providers groq,vllm_remote \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "orchestration tax: gsm8k, groq vs vllm, 10 reps" \
  --save-db
```

Run during a low-activity window on the machine. For direct energy
measurement to be meaningful, background load must be stable. The idle
baseline captured at install time subtracts background power, but
active background processes add noise.

For overnight runs, use a wrapper:

```bash
nohup python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic,logical_reasoning,code_fibonacci \
  --repetitions 20 \
  --providers groq \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "your research question" \
  --save-db > /tmp/alems_run.log 2>&1 &

tail -f /tmp/alems_run.log
```

---

## Stage 6: Verify Data Quality

After the run completes, check data quality before any analysis:

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

# Check run completion and coverage
sqlite3 "$DB" "
SELECT
  r.workflow_type,
  COUNT(*) as runs,
  AVG(r.energy_sample_coverage_pct) as avg_coverage,
  SUM(CASE WHEN r.energy_sample_coverage_pct < 80 THEN 1 ELSE 0 END) as low_coverage,
  AVG(r.total_energy_uj) as avg_energy_uj
FROM runs r
JOIN experiments e ON r.exp_id = e.exp_id
WHERE e.experiment_goal LIKE '%your research question%'
GROUP BY r.workflow_type;
"

# Check for confirmed outliers
sqlite3 "$DB" "
SELECT ro.run_id, ro.outlier_class, ro.severity,
       ro.metric_name, ro.review_status
FROM run_outliers ro
JOIN runs r ON ro.run_id = r.run_id
JOIN experiments e ON r.exp_id = e.exp_id
WHERE e.experiment_goal LIKE '%your research question%'
  AND ro.review_status = 'confirmed'
ORDER BY ro.run_id;
"
```

**Coverage below 80%:** the energy sampler had gaps during that run.
Exclude runs with `energy_sample_coverage_pct < 80` from paper datasets.

**Confirmed outliers:** A-LEMS never auto-excludes. An outlier is flagged
with `review_status = 'pending'`. Review it, then set `confirmed` to
remove it from clean views, or `dismissed` to keep it. Clean views
(`v_runs_clean_*`) exclude only confirmed outliers.

---

## Stage 7: Analyze Results

Use the clean views for analysis. Never query the raw `runs` table
directly for paper datasets — use the view appropriate to your domain:

```sql
-- Energy analysis: excludes all confirmed outliers
SELECT * FROM v_runs_clean_energy
WHERE experiment_goal LIKE '%your research question%';

-- CPU analysis: excludes data quality failures, keeps statistical anomalies
SELECT * FROM v_runs_measured_cpu
WHERE experiment_goal LIKE '%your research question%';
```

Key metrics for common research questions:

**Phase energy breakdown (agentic runs):**
```sql
SELECT
  e.provider,
  AVG(r.planning_energy_uj)   as avg_planning_uj,
  AVG(r.execution_energy_uj)  as avg_execution_uj,
  AVG(r.synthesis_energy_uj)  as avg_synthesis_uj,
  AVG(r.total_energy_uj)      as avg_total_uj,
  COUNT(*)                     as n
FROM v_runs_clean_energy r
JOIN experiments e ON r.exp_id = e.exp_id
WHERE r.workflow_type = 'agentic'
  AND e.experiment_goal LIKE '%your research question%'
GROUP BY e.provider;
```

**Energy per token across providers:**
```sql
SELECT
  e.provider,
  r.workflow_type,
  AVG(r.energy_per_token)   as avg_uj_per_token,
  AVG(r.dynamic_energy_uj)  as avg_dynamic_uj,
  COUNT(*)                   as n
FROM v_runs_clean_energy r
JOIN experiments e ON r.exp_id = e.exp_id
WHERE r.energy_per_token IS NOT NULL
  AND e.experiment_goal LIKE '%your research question%'
GROUP BY e.provider, r.workflow_type
ORDER BY avg_uj_per_token;
```

**Linear vs agentic energy comparison:**
```sql
SELECT
  r1.run_id as linear_run,
  r2.run_id as agentic_run,
  r1.total_energy_uj as linear_uj,
  r2.total_energy_uj as agentic_uj,
  r2.total_energy_uj - r1.total_energy_uj as overhead_uj,
  ROUND(100.0 * (r2.total_energy_uj - r1.total_energy_uj)
        / r2.total_energy_uj, 1) as overhead_pct
FROM runs r1
JOIN runs r2 ON r1.exp_id = r2.exp_id
WHERE r1.workflow_type = 'linear'
  AND r2.workflow_type = 'agentic'
ORDER BY r1.run_id DESC LIMIT 10;
```

---

## Stage 8: Write the Methodology Section

Every paper using A-LEMS measurements must include a methodology
section that states:

1. The `platform_class` and `energy_measurement` tier
2. The hardware counter used (RAPL, SPBM, IOKit, etc.)
3. The idle baseline method and duration
4. The number of repetitions and the outlier exclusion criteria
5. The `experiment_type` and what it controls for

Template for a direct energy platform:

> Energy was measured on `nvidia_grace` hardware using SPBM hwmon
> energy counters at 100 Hz. All values represent dynamic energy after
> subtraction of a 90-second idle baseline measured with CPU pinned to
> background load and 2-sigma outlier rejection. Runs with
> `energy_sample_coverage_pct < 80` were excluded. Statistical outliers
> (modified Z-score, MAD method, minimum population 10 runs) were
> reviewed manually and excluded only when confirmed as data quality
> failures. N = [your repetition count] runs per condition.

Template for a modeled platform:

> This experiment was conducted on `linux_arm` hardware. Hardware
> energy counters are not accessible on this platform
> (`energy_measurement: modeled`). Energy values are recorded as zero
> in the dataset. Compute metrics (instructions, cycles, IPC) are
> available via ARM PMU sysfs and are reported instead.

See [Measurement Methodology](../research/measurement-methodology.md)
for per-platform methodology templates and
[Energy Tiers](../concepts/energy-tiers.md) for the full tier taxonomy.

---

## Stage 9: Export Data for Analysis

Export a clean dataset for external analysis tools:

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

sqlite3 -csv "$DB" "
SELECT
  r.run_id,
  e.experiment_goal,
  e.provider,
  e.task_name,
  r.workflow_type,
  r.total_energy_uj,
  r.dynamic_energy_uj,
  r.attributed_energy_uj,
  r.planning_energy_uj,
  r.execution_energy_uj,
  r.synthesis_energy_uj,
  r.total_tokens,
  r.duration_ns,
  r.complexity_score,
  r.energy_sample_coverage_pct,
  r.energy_measurement_mode
FROM v_runs_clean_energy r
JOIN experiments e ON r.exp_id = e.exp_id
WHERE e.experiment_goal LIKE '%your research question%'
ORDER BY r.run_id;
" > my_experiment_data.csv
```

---

## Stage 10: Cite the Platform

Include the A-LEMS citation in your paper:

```bibtex
@software{panigrahy2026alems,
  title   = {A-LEMS: Agentic LLM Energy Measurement System},
  author  = {Panigrahy, Deepak},
  year    = {2026},
  url     = {https://github.com/deepakpanigrahy03/alems-platform}
}
```
