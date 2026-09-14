# Platform Settings Reference

---
**File:** `config/app_settings.yaml`
**Schema version:** 89+
**Status:** PRODUCTION
**Last updated:** 2026-09-14
---

## Overview

`config/app_settings.yaml` is the central configuration file for the
A-LEMS measurement platform. It controls the database backend, experiment
parameters, measurement timing, extension activation, and the web UI.

This file lives inside the project directory and is committed to version
control. It contains no secrets and no machine-specific paths — those
belong in `~/.alemsrc` and `config/machines/<hostname>.yaml` respectively.

Every setting has a documented default. You only need to change a setting
when the default does not fit your research setup.

---

## Quick Reference

```yaml
# config/app_settings.yaml — annotated structure

database:          # storage backend selection
experiment:        # measurement parameters and baseline control
msr:               # hardware performance counter settings
logging:           # log level, rotation, retention
alerts:            # thermal and power thresholds
webui:             # streaming dashboard configuration
extensions:        # research extension activation (35D)
```

---

## database

Controls where A-LEMS stores measurement data.

```yaml
database:
  engine: "sqlite"        # "sqlite" or "postgresql"

  sqlite:
    db_name: "experiments.db"   # filename only — full path resolved by path_loader
    journal_mode: "WAL"         # WAL gives better read concurrency during experiments
    timeout: 30                 # seconds before connection gives up
    detect_types: 1             # enables Python type detection on read

  postgresql:
    host: "localhost"
    port: 5432
    database: "alems"
    user: "alems_user"
    password: "${DB_PASSWORD}"  # always use an environment variable here
    pool_size: 10
    max_overflow: 20
    pool_timeout: 30

  pool_pre_ping: true           # verify connection before each use
  backup_enabled: true
  backup_interval_hours: 24
```

**The actual database file path** is not set here. It is resolved by
`scripts/tools/path_loader.py` using this priority order:

```
1. ALEMS_DATA_ROOT environment variable (set in ~/.alemsrc)
2. Default: <project_root>/data/experiments.db
```

Set `ALEMS_DATA_ROOT` in `~/.alemsrc` when your storage is on a separate
mount. Leave it unset to use the project default.

**Switching to PostgreSQL:** change `engine: "postgresql"`, fill in the
`postgresql` block, set `DB_PASSWORD` in your environment, and run
`python3 scripts/tools/alems_migrate.py`. No other code changes needed.

---

## experiment

Controls how experiments run and how idle baselines are measured.

```yaml
experiment:
  default_iterations: 10      # repetitions per task when not specified on CLI
  cool_down_seconds: 30       # wait between repetitions for thermal recovery
  parallel_execution: true    # run linear and agentic workflows concurrently
  max_concurrent_tasks: 2     # max parallel task workers
  save_raw_data: true         # keep raw energy samples (energy_samples table)

  baseline:
    force_remeasure: false    # true = always re-measure idle baseline at startup
                              # false = use cached baseline from cache_file
    duration_seconds: 30      # how long to measure idle power
    num_samples: 3            # how many baseline measurements to average
    pre_wait_seconds: 10      # settle time before baseline measurement starts
    cache_file: "data/idle_baseline.json"
    measure_gpu: true         # include GPU in baseline measurement
```

**`force_remeasure`** is the most commonly changed setting. Set it to
`true` when:
- The machine has been rebooted and thermal state has changed
- You suspect the cached baseline is stale
- You are starting a new paper data collection session

Set it back to `false` during development to avoid the 30-second baseline
wait on every test run.

**`cool_down_seconds`** matters for thermal accuracy. On GN100 running
back-to-back agentic experiments, 30 seconds allows the SoC to return
to within 2°C of idle. For multi-repetition paper data collection,
increase to 60 seconds.

---

## msr

Controls hardware performance counter collection via MSR registers.

```yaml
msr:
  enable_aperf_mperf: false   # true = read APERF/MPERF for actual CPU frequency
                              # adds ~2ms overhead per measurement interval
  wakeup_latency_iterations: 500   # samples for wake-up latency baseline
  measure_cores: [0]          # which CPU cores to measure (future: multi-core)
  parallel_measurement: false  # future: measure cores in parallel
```

**`enable_aperf_mperf`** is disabled by default because it adds measurement
overhead. Enable it when frequency scaling behavior is the research question,
not when energy is the primary metric. The overhead is small but measurable
at sub-second granularity.

---

## logging

```yaml
logging:
  level: INFO       # DEBUG, INFO, WARNING, ERROR
  format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
  rotation: 1 day
  retention: 30 days
```

Set `level: DEBUG` when diagnosing measurement issues. The debug output
shows reader selection, sample counts, and ETL pipeline steps.
Switch back to `INFO` for paper data collection — debug output adds
overhead and fills disk quickly on long experiments.

Log file location is set by:

```yaml
paths:
  log: logs/a-lems.log
```

---

## alerts

Thresholds that trigger warnings in the experiment log.

```yaml
alerts:
  temperature_threshold_celsius: 85   # warn if SoC exceeds this during measurement
  power_threshold_watts: 50           # warn if package power exceeds this at idle
  memory_threshold_mb: 3500           # warn if RSS exceeds this
```

These do not stop experiments — they add WARNING lines to the log.
The temperature threshold is conservative for GN100 (max junction is 105°C).
Raise it to 90 for sustained multi-hour data collection if you are
monitoring thermal throttling explicitly.

---

## webui

Controls the streaming dashboard that shows live energy data during experiments.

```yaml
webui:
  enabled: true
  servers:
    - name: "live_view"
      type: "streamlit"
      url: "http://localhost:8501"
      api_endpoint: "/api/update"
      active: true           # this server receives live updates

    - name: "dashboard"
      type: "grafana"
      url: "http://localhost:3000"
      api_endpoint: "/api/live"
      active: false          # disabled — set true when Grafana is running

    - name: "custom"
      type: "generic"
      url: "http://localhost:8080"
      api_endpoint: "/metrics"
      active: false

  sampling_rate_hz: 10    # how often live data is pushed to active servers
  timeout_ms: 1           # dashboard push timeout — keep low to not block measurement
```

The `active: true/false` flags under each server are for the web UI
servers, not for research extensions. These are completely separate from
the `extensions:` section described below.

Set `enabled: false` to disable all dashboard pushing entirely. This is
useful on headless machines where no dashboard is running.

---

## extensions

Controls which research extensions are active on this machine.

```yaml
extensions:
  active:
    - output_quality      # LLM-as-judge quality scoring (chunk 8.5C)
    - orchestration       # EpG/OOI orchestration tax (Paper 1)
```

**This section is optional.** When it is absent entirely, the platform
runs in legacy mode: all extension tables receive data through the
existing direct-write code paths, identical to behavior before the
extension system shipped.

**When this section is present**, the platform runs in selective mode:
only the extensions listed under `active` have their runtime callbacks
registered. Unlisted extensions' tables remain in the database with all
historical data intact, but stop receiving new rows.

### Activating an extension

```bash
# 1. Add the extension name to the active list
# 2. Run the migration runner — creates the extension's tables if needed
python3 scripts/tools/alems_migrate.py

# 3. The next experiment run activates the extension automatically
```

### Deactivating an extension

Remove the name from the active list. No migration needed.
The extension's tables and all historical data are preserved.
You can query them directly via SQL at any time.

### Legacy mode vs selective mode

```
No [extensions] section:
  → legacy mode
  → all extension tables written as before
  → zero behavior change on existing machines

[extensions] active = [...]:
  → selective mode
  → only listed extensions write new data
  → unlisted extension tables retain historical data
  → core measurements identical in both modes
```

### Extension version namespace in migration_history

Extension migrations use version numbers 90001 and above in
`migration_history`. This distinguishes them from core schema
migrations (1-9000) at a glance:

```sql
SELECT version, filename, source FROM migration_history ORDER BY version;
-- version 89    → core schema (v089_extension_registry.sql)
-- version 9000  → core adoption migration
-- version 90001 → ext:output_quality e001
```

---

## ALEMS_PLATFORM_OVERRIDE

This is not in `app_settings.yaml` — it is an environment variable.
It is documented here because it interacts with the settings above.

```bash
export ALEMS_PLATFORM_OVERRIDE=synthetic
```

When set, all hardware readers are replaced with synthetic readers that
return fixed fixture values from `data/fixtures/synthetic_energy.yaml`.
Energy measurements are deterministic and do not require physical hardware.

**When to use it:**
- Running CI pipelines without measurement hardware
- Testing new extensions without needing a real machine
- Verifying migration runner logic in isolation

**When NOT to use it:**
- Any experiment that produces paper data
- Any experiment where energy values matter
- Any session where you forget to unset it afterward

**Always unset after testing:**

```bash
unset ALEMS_PLATFORM_OVERRIDE

# Verify it is gone before running real experiments
echo "OVERRIDE=${ALEMS_PLATFORM_OVERRIDE:-NOT SET}"
# Expected: OVERRIDE=NOT SET
```

If `ALEMS_PLATFORM_OVERRIDE=synthetic` is set during a real experiment,
the platform selects the synthetic reader and returns fixture energy values.
The experiment completes without error but records wrong energy numbers.
The only symptom is `0.0000 J` in the summary (because the fixture value
is small and rounds to zero at 4 decimal places in the display).

Add this check to your pre-experiment checklist:

```bash
# Pre-experiment checklist
echo "1. OVERRIDE: ${ALEMS_PLATFORM_OVERRIDE:-NOT SET}"
python3 -c "
from core.utils.platform import get_platform_capabilities
caps = get_platform_capabilities()
print('2. Platform:', caps.platform_class)
print('3. Mode:', caps.measurement_mode)
"
# Expected on GN100:
# 1. OVERRIDE: NOT SET
# 2. Platform: nvidia_grace
# 3. Mode: MEASURED
```

---

## Full Example: GN100 Production Config

This is the complete `app_settings.yaml` for the NVIDIA Grace GB10 platform
running production paper data collection:

```yaml
database:
  engine: "sqlite"
  sqlite:
    db_name: "experiments.db"
    journal_mode: "WAL"
    timeout: 30
    detect_types: 1
  pool_pre_ping: true
  backup_enabled: true
  backup_interval_hours: 24

paths:
  log: logs/a-lems.log
  data: data/
  exports: exports/
  temp: tmp/

timeouts:
  execution_seconds: 300
  api_seconds: 30
  measurement_seconds: 600
  database_seconds: 10

experiment:
  default_iterations: 10
  cool_down_seconds: 30
  parallel_execution: true
  max_concurrent_tasks: 2
  save_raw_data: true
  baseline:
    force_remeasure: false
    duration_seconds: 30
    num_samples: 3
    pre_wait_seconds: 10
    cache_file: "data/idle_baseline.json"
    measure_gpu: true

msr:
  enable_aperf_mperf: false
  wakeup_latency_iterations: 500
  measure_cores: [0]
  parallel_measurement: false

logging:
  level: INFO
  format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
  rotation: 1 day
  retention: 30 days

alerts:
  temperature_threshold_celsius: 85
  power_threshold_watts: 50
  memory_threshold_mb: 3500

webui:
  enabled: true
  servers:
    - name: "live_view"
      type: "streamlit"
      url: "http://localhost:8501"
      api_endpoint: "/api/update"
      active: true
    - name: "dashboard"
      type: "grafana"
      url: "http://localhost:3000"
      api_endpoint: "/api/live"
      active: false
    - name: "custom"
      type: "generic"
      url: "http://localhost:8080"
      api_endpoint: "/metrics"
      active: false
  sampling_rate_hz: 10
  timeout_ms: 1

# Research extensions active on this machine.
# Remove this section entirely to return to legacy mode.
extensions:
  active: []
```

---

## Settings That Should Never Be Changed

These settings exist in the file but have values that the platform
depends on structurally. Changing them will break measurement correctness:

| Setting | Value | Why fixed |
|---|---|---|
| `sqlite.journal_mode` | `WAL` | Required for concurrent reads during measurement |
| `sqlite.detect_types` | `1` | Required for correct Python type mapping on read |
| `experiment.save_raw_data` | `true` | Raw samples are needed for all ETL and analysis |
| `logging.format` | (as shown) | Parsed by log analysis scripts |

---

## Related Documentation

- Configuration hierarchy and DB path resolution: `developer/config-hierarchy.md`
- Extension system developer guide: `developer/extension-system.md`
- Schema migration system: `developer/schema-migration.md`
- Adding a new extension: `developer/extension-system.md#5-building-your-first-extension`
