# Measurement Model

A-LEMS is a config-driven measurement platform. Models, tasks, providers,
experiment parameters, quality thresholds, display configuration, and query
definitions are all externalized into configuration files and database tables.
No experiment parameter is hardcoded. Adding a new model, task, or provider
requires only a configuration change.

---

## Config-Driven Architecture

The system separates three concerns that most measurement tools conflate:

**What to measure** is defined in `config/tasks.yaml`. Each task specifies
a prompt structure, expected output type, difficulty level, and tool
requirements. Tasks are loaded into the `task_categories` DB table at install
time and referenced by ID at experiment time.

**How to run it** is defined in `config/models.yaml`. Each provider entry
specifies transport (in-process, loopback HTTP, remote HTTP), authentication,
base URL, rate limits, cost class, and the list of models it serves. The
model factory reads this file at runtime; no provider-specific code exists
outside the factory and the adapter classes.

**How to measure it** is defined by the platform's `capability_profile` and
the `measurement_methodology` DB table seeded from `seed_methodology.py`.
Every measurement method has a unique ID, a layer assignment, a confidence
level, and a LaTeX formula. Every column in the `runs` table maps to exactly
one method ID via `COLUMN_PROVENANCE` in `core/utils/provenance.py`.

---

## Configuration Files

| File | Purpose | Loaded into DB? |
|---|---|---|
| `config/models.yaml` | Provider and model registry | No — read at runtime |
| `config/tasks.yaml` | Task definitions | Yes — `task_categories` table |
| `config/app_settings.yaml` | Server, database, experiment defaults | No — read at startup |
| `config/metric_registry.yaml` | All metrics: provenance, formula, layer | Yes — `metric_display_registry` |
| `config/query_registry.yaml` | All SQL queries — no SQL in application code | Yes — `query_registry` table |
| `config/quality.yaml` | Quality thresholds per task | Yes — `task_quality_config` table |
| `config/methodology_refs/*.yaml` | Literature references per method | Yes — `method_references` table |
| `config/experiment_templates.yaml` | Reusable experiment configurations | No — read at experiment time |
| `config/country_metrics.yaml` | Grid intensity factors per country | Yes — `normalization_factors` |
| `config/paths.yaml` | Documentation and tool output paths | No — read by PathConfig |
| `config/hw_config.json` | Platform identity (machine-specific, gitignored) | Yes — `hardware_config` table |

---

## Measurement Layers

Every metric in A-LEMS is assigned to one of four layers. The layer
determines where in the stack the measurement originates and what
attribution model applies.

| Layer | What it covers | Examples |
|---|---|---|
| `silicon` | Hardware counters, physical energy | RAPL energy_uj, ARM PMU instructions, cache misses |
| `os` | Operating system observability | /proc/stat ticks, context switches, memory RSS |
| `application` | LLM API timing and token counts | TTFT, TPOT, prompt tokens, completion tokens |
| `orchestration` | Agentic workflow coordination | Planning energy, tool call energy, synthesis energy, orchestration tax |

---

## Provenance Chain

Every value in the `runs` table traces to a hardware counter through an
unbroken provenance chain. This chain is machine-readable and stored in the
database alongside the measurement values.

```
Hardware counter read
    ↓
Raw measurement (energy_samples, cpu_samples, etc.)
    ↓
Derived value (phase attribution, normalization)
    ↓
runs table column
    ↓
COLUMN_PROVENANCE entry (method_id, provenance_type)
    ↓
measurement_methodology entry (name, layer, confidence, formula_latex)
    ↓
method_references entry (literature citation)
```

Provenance types:

| Type | Meaning |
|---|---|
| `MEASURED` | Direct hardware or OS read, no mathematics |
| `CALCULATED` | Deterministic formula applied to measured values |
| `INFERRED` | Uses external constants, emission factors, or models |
| `MODELED` | Proportionality model applied to measured values |
| `SYSTEM` | Infrastructure metadata, no scientific meaning |

---

## Sample Tables

A-LEMS records raw samples throughout each experiment at fixed frequencies.
These are never computed at read time. ETL pipelines aggregate them into
the `runs` table after the experiment completes.

| Table | Frequency | Source | Content |
|---|---|---|---|
| `energy_samples` | 100 Hz | RAPL / SPBM / IOKit | Cumulative energy counters per domain |
| `cpu_samples` | 10 Hz | turbostat + perf | Frequency, IPC, L1/L2/L3 cache counters |
| `interrupt_samples` | 10 Hz | /proc/stat | Interrupt and context switch ticks |
| `io_samples` | 10 Hz | /proc/diskstats | Disk read/write byte deltas |
| `thermal_samples` | 1 Hz | hwmon sensors | Temperature, fan RPM, voltage |
| `power_rail_samples` | variable | SPBM hwmon | Per-rail power in watts (NVIDIA Grace) |
| `gpu_samples` | variable | DCGM | GPU utilization and memory (NVIDIA Grace) |
| `nic_samples` | 10 Hz | /proc/net/dev | Network bytes sent and received |
| `cooling_samples` | 1 Hz | /sys/class/thermal | Cooling device state and target |

---

## ETL Pipeline

Raw samples alone are not sufficient for paper-quality analysis. Two ETL
pipelines run asynchronously after each experiment pair completes:

**Phase attribution ETL** (`phase_attribution_etl.py`) slices the energy
timeline by orchestration phase. Planning, tool execution, and synthesis
each receive an attributed energy value derived from the energy sample
timestamps and the orchestration event log.

**Hardware aggregation ETL** (`aggregate_hardware_metrics.py`) aggregates
sample tables into single-value summary columns in the `runs` table:
total cache misses, average voltage, total disk bytes, peak temperature.

Both ETLs read from sample tables and write to `runs` table columns that
are `NULL` at INSERT time. The `etl_queue` table tracks pending and
completed ETL jobs.

---

## Idle Baseline Subtraction

On direct energy platforms, every experiment subtracts an idle baseline
from the reported energy values. The baseline captures background power
consumption: OS daemons, DRAM refresh, uncore clocks, GPU idle draw.

The baseline is measured at install time (`Step 12`) and stored in the
`idle_baselines` table with fields for package, core, uncore, DRAM, and
GPU power in watts plus standard deviation per domain. The `baseline_id`
column in every run record links to the baseline that was active when the
run executed.

Reported energy:

```
dynamic_energy_uj = total_energy_uj - (baseline_power_watts × duration_seconds × 1,000,000)
```

This subtraction is applied per domain (package, core, uncore, DRAM, GPU)
and is documented in the methodology entry for each energy column.

---

## Experiment Types

The `--experiment-type` argument is not just a label. It is stored in the
`runs` table and used by the quality system to apply different validation
thresholds per research intent.

| Type | Research intent |
|---|---|
| `normal` | Standard measurement run |
| `overhead_study` | Quantifying orchestration or framework overhead |
| `retry_study` | Studying retry behavior and energy cost of failures |
| `failure_injection` | Controlled tool failure experiments |
| `quality_sweep` | Varying quality parameters across runs |
| `calibration` | Baseline or hardware calibration runs |
| `ablation` | Ablation study — systematic feature removal |
| `pilot` | Exploratory run before committing to full study |
| `debug` | Development and debugging — excluded from paper datasets |
