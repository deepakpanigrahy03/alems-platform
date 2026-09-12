# Configuration System

A-LEMS uses a three-scope configuration system. Each scope has a
specific purpose and a specific location. No configuration crosses
scope boundaries.

---

## Three Scopes

### Machine Scope — ~/.alemsrc

Set once per machine. Contains paths, API keys, and URLs that are
specific to the physical machine. Written by `install.sh` and never
committed to any repository.

```bash
export ALEMS_DATA_ROOT=/mnt/alems-data
export ALEMS_MODELS_DIR=/home/dpani/models
export GROQ_API_KEY=gsk_...
export NVIDIA_API_KEY=nvapi-...
export ALEMS_VLLM_REMOTE_URL=http://100.84.85.2:8000/v1
```

`path_loader.py` sources `~/.alemsrc` automatically before resolving
any path. Scripts do not need to pre-source it.

### Checkout Scope — .alems-env

Set once per checkout. Contains the environment name and an optional
data root override. Written by `install.sh` and gitignored.

```
ALEMS_ENV=prod
ALEMS_DATA_ROOT=/override/path    # optional, overrides ~/.alemsrc
```

Supported environments: `dev`, `integration`, `preprod`, `prod`.

On fresh installs from the `main` branch, the installer suggests `dev`.
On production measurement servers, use `prod`.

### Experiment Scope — Database

Captured at run time. Provider, country, methodology, baseline ID,
and all measurement values are written to the database at experiment
time. This scope cannot be configured before a run — it is recorded
as the run executes.

---

## YAML Config Files

YAML files in `config/` describe behavior, not deployment. They contain
model parameters, task definitions, quality thresholds, and display
configuration. They never contain machine-specific paths, API keys,
or hostnames.

| File | Purpose |
|---|---|
| `config/models.yaml` | Provider and model registry |
| `config/tasks.yaml` | Task definitions |
| `config/app_settings.yaml` | Server, database, experiment defaults |
| `config/metric_registry.yaml` | Metric display configuration |
| `config/query_registry.yaml` | SQL query registry |
| `config/quality.yaml` | Quality thresholds per task |
| `config/methodology_docs.yaml` | Methodology documentation map |
| `config/paths.yaml` | Documentation and tool output paths |
| `config/hw_config.json` | Platform identity (machine-specific, gitignored) |

YAML files support `$VAR` expansion via `os.path.expandvars()` at
load time. The `${VAR:-default}` bash syntax is not supported.
Defaults belong in `~/.alemsrc`, not in YAML.

---

## Database Path Resolution

`path_loader.py` resolves the database path through four layers:

```
Layer 0: .alems-env (highest priority)
         ALEMS_ENV=prod → prod path formula
         ALEMS_ENV=dev  → dev path formula

Layer 1: ALEMS_DATA_ROOT from environment or ~/.alemsrc

Layer 2: app_settings.yaml database.sqlite.db_name

Layer 3: data/experiments.db (fallback)
```

**Path formulas:**

```
prod: $ALEMS_DATA_ROOT/<hostname>/envs/prod/experiments.db
dev:  $ALEMS_DATA_ROOT/<hostname>/envs/<user>/dev/<project>/experiments.db
```

**Debug the resolved path:**

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
"
```

See [DB Path Resolution](../reference/db-path-resolution.md) for
full debugging commands.

---

## Config Loading at Runtime

The model factory reads `config/models.yaml` at experiment startup.
Task definitions are read from the `task_categories` database table
(seeded from `config/tasks.yaml` at install time). Methodology entries
are read from `measurement_method_registry` (seeded from
`seed_methodology.py`).

This means: changing `models.yaml` takes effect immediately on the
next experiment run. Changing `tasks.yaml` requires re-running
`python3 scripts/migrate_yaml_to_db.py` to reload the DB table.

---

## What Goes Where

| Config type | Location |
|---|---|
| API keys | `~/.alemsrc` only |
| Server URLs | `~/.alemsrc` only |
| Data paths | `~/.alemsrc` only |
| Environment name | `.alems-env` only |
| Model parameters | `config/models.yaml` |
| Task prompts | `config/tasks.yaml` |
| Quality thresholds | `config/quality.yaml` |
| Measurement methods | `measurement_method_registry` table |
| Provider/country | `runs` table (captured at experiment time) |

---

## Reloading Config After Changes

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

# Reload YAML configs to DB (tasks, queries, metrics)
python3 scripts/migrate_yaml_to_db.py

# Re-seed methodology
python3 scripts/seed_methodology.py

# Apply any pending schema migrations
alems dev sync
```
