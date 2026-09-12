# CLI Reference

A-LEMS provides a single entry point for all operations: the `alems`
command. It is installed as a shell function in `~/.bashrc` during
`bash scripts/install.sh` and auto-discovers the project root by
walking up the directory tree.

---

## Developer Commands

### alems dev status

Show the current environment state.

```bash
alems dev status
```

Output:

```
  ┌─────────────────────────────────────────────────┐
  │  A-LEMS Environment Status                      │
  └─────────────────────────────────────────────────┘
  Platform:  nvidia_grace
  Env:       prod
  DB:        /mnt/alems-data/gn100-2b96/envs/prod/experiments.db
  Branch:    main
  Commit:    a1b2c3d
  Schema:    v086
  Python:    Python 3.12.3
```

### alems dev sync

Sync schema and configs without pulling from git. Runs migrations,
reloads YAML configs to DB, re-seeds methodology and quality config.

```bash
alems dev sync
```

### alems dev pull

Git pull followed by smart sync. Only runs migration and config reload
steps if the relevant files changed between old and new HEAD.

```bash
alems dev pull
```

### alems dev install

Run the full install script. Use for fresh installs or re-installs.

```bash
alems dev install
```

---

## Experiment Commands

### alems run

Run an experiment. Calls `run_experiment.py` with the given task ID
and any additional arguments.

```bash
alems run <task-id> [args]
```

Example:

```bash
alems run gsm8k_basic
```

Note: for full control over experiment parameters, call
`run_experiment.py` directly:

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 3 \
  --provider groq \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "your research question" \
  --save-db
```

### alems measure

Run an idle energy baseline measurement. Used after hardware changes
or when the baseline needs to be refreshed.

```bash
alems measure
```

### alems report

Generate an experiment report.

```bash
alems report
```

---

## run_experiment.py Arguments

| Argument | Default | Description |
|---|---|---|
| `--tasks` | required | Task ID or comma-separated list |
| `--provider` | `llama_cpp` | Provider name from models.yaml |
| `--providers` | none | Comma-separated providers for multi-provider runs |
| `--repetitions` / `-n` | 1 | Number of repetitions per task |
| `--cool-down` | 30 | Seconds between repetitions |
| `--workflow-mode` | `comparison` | `linear`, `agentic`, or `comparison` |
| `--experiment-type` | `normal` | Research intent label |
| `--experiment-goal` | none | Human readable research question |
| `--save-db` | false | Write results to database |
| `--verbose` | false | Print hardware counters per repetition |
| `--no-warmup` | false | Skip warmup run |
| `--list-tasks` | — | Print all available tasks and exit |
| `--config` | none | Path to experiment config YAML |

### --experiment-type values

| Type | Use |
|---|---|
| `normal` | Standard measurement run |
| `overhead_study` | Quantifying orchestration or framework overhead |
| `retry_study` | Studying retry behavior and energy cost |
| `failure_injection` | Controlled tool failure experiments |
| `quality_sweep` | Varying quality parameters |
| `calibration` | Baseline or hardware calibration |
| `ablation` | Ablation study |
| `pilot` | Exploratory run before full study |
| `debug` | Development — excluded from paper datasets |

---

## build-docs.sh Arguments

| Argument | Description |
|---|---|
| `--validate-only` | Run validator only, no diagram generation or build |
| `--serve` | Start mkdocs serve after build |
| `--strict` | Treat validator warnings as failures |

```bash
# Validate only — safe to run anytime
bash scripts/build-docs.sh --validate-only

# Full build
bash scripts/build-docs.sh

# Build and serve
bash scripts/build-docs.sh --serve
```

---

## alems_migrate.py Arguments

| Argument | Description |
|---|---|
| `--status` | Print migration history |
| `--check` | Validate manifest checksums |
| `--plan` | Dry run — show what would be applied |
| `--verify` | Checksum verification only |
| `--adopt` | One-time baseline adoption |

```bash
python3 scripts/tools/alems_migrate.py --status
python3 scripts/tools/alems_migrate.py --plan
```
