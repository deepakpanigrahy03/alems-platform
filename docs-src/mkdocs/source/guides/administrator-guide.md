# Administrator Guide

This guide covers managing a multi-machine A-LEMS deployment: setting
up production and development environments, backing up databases,
keeping machines in sync, and adding new machines to the fleet.

---

## Environment Strategy

A-LEMS uses two environments for each machine:

**`prod`** — the production measurement environment. One shared database
per machine. All paper-quality experiments go here. Schema migrations
in prod fail fatally on checksum mismatch — no silent schema drift.
Database path: `$ALEMS_DATA_ROOT/<hostname>/envs/prod/experiments.db`

**`dev`** — isolated per developer and per project. Database path:
`$ALEMS_DATA_ROOT/<hostname>/envs/<user>/dev/<project>/experiments.db`
Multiple developers can work simultaneously without interfering.
Schema drift in dev is healed with a warning, not a fatal error.

Set the environment in `.alems-env` in the project root:

```
ALEMS_ENV=prod
```

Never run exploratory or debug experiments in prod. Use dev for
development work, pilots, and debugging. Use prod only for experiments
that generate paper-quality data.

---

## Adding a New Machine

**Step 1: Clone the repository**

```bash
git clone https://github.com/deepakpanigrahy03/alems-platform.git
cd alems-platform
```

**Step 2: Run the installer**

```bash
bash scripts/install.sh
```

The installer detects the platform, installs dependencies, sets
hardware counter permissions, initializes the database, and measures
the idle baseline. It asks two questions: environment (use `prod` for
measurement machines) and data root path.

**Step 3: Verify**

```bash
source ~/.bashrc
bash scripts/alems dev status
python3 scripts/tools/validate_methodology_refs.py
```

**Step 4: Add to fleet documentation**

Update `reference/platform-matrix.md` with the new machine's
`platform_class`, hardware, and energy stack. This keeps the public
documentation current with the actual fleet.

---

## Database Backup

Back up the production database regularly. A-LEMS has no built-in
backup scheduler — use cron or a backup script.

**Manual backup:**

```bash
cd ~/mydrive/alems-platform && source ~/.alemsrc && source venv/bin/activate

DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

BACKUP_DIR="$ALEMS_DATA_ROOT/$(hostname)/backups"
mkdir -p "$BACKUP_DIR"
sqlite3 "$DB" ".backup $BACKUP_DIR/experiments_$(date +%Y%m%d_%H%M%S).db"
echo "Backup: $BACKUP_DIR"
```

**Cron backup (daily at 2am):**

```bash
crontab -e
# Add:
0 2 * * * cd ~/mydrive/alems-platform && source venv/bin/activate && \
  sqlite3 $(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.tools.path_loader import get_alems_db_path; print(get_alems_db_path())") \
  ".backup $ALEMS_DATA_ROOT/$(hostname)/backups/experiments_$(date +\%Y\%m\%d).db"
```

**Verify backup integrity:**

```bash
sqlite3 "$BACKUP_DIR/experiments_<date>.db" "PRAGMA integrity_check;"
```

---

## Keeping Machines in Sync

All machines in the fleet must run the same schema version. After
any migration is applied on one machine, apply it on all machines
before running cross-platform experiments.

**Check schema version on current machine:**

```bash
bash scripts/alems dev status
# Shows: Schema: v088
```

**Apply pending migrations:**

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
git pull origin main
python3 scripts/tools/alems_migrate.py
```

**Check for migration failures:**

```bash
python3 scripts/tools/alems_migrate.py --status | grep -v "applied"
```

Any row that is not `applied` is a problem. Investigate before running
experiments.

---

## Production Migration Protocol

In `prod` environment, migrations are immutable. Never edit an applied
migration file. If a migration has an error, create a new migration
that corrects it — do not modify the original.

Before applying any migration to prod:

1. Test on a dev environment first
2. Take a database backup
3. Apply with `python3 scripts/tools/alems_migrate.py`
4. Verify with `python3 scripts/tools/alems_migrate.py --status`
5. Run one experiment to confirm the application works correctly

If a migration fails on prod, the database is in an intermediate state.
Restore from backup immediately. Do not attempt to continue.

---

## Hardware Config Refresh

When hardware changes on a machine (new GPU, kernel upgrade, BIOS
update), regenerate `hw_config.json`:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/detect_hardware.py
```

Verify the capability profile updated correctly:

```bash
python3 -c "
import json
with open('config/hw_config.json') as f:
    cfg = json.load(f)
print('platform_class:     ', cfg.get('platform_class'))
print('energy_measurement: ', cfg.get('energy_measurement'))
print('hw_config_version:  ', cfg.get('hw_config_version'))
print('capability_profile: ', cfg.get('capability_profile'))
"
```

After any hardware change that affects power consumption, re-run the
installer to capture a fresh idle baseline:

```bash
bash scripts/install.sh
```

The installer detects the existing configuration and skips steps that
do not need to repeat. Step 12 (idle baseline measurement) always runs
and updates the `idle_baselines` table.

---

## Shared Data Root

For a fleet where multiple machines write to a shared network storage,
configure each machine's `ALEMS_DATA_ROOT` to point to the shared mount:

```bash
# In ~/.alemsrc on each machine
export ALEMS_DATA_ROOT=/shared/alems-data
```

Each machine writes to its own subdirectory:
```
/shared/alems-data/
    gn100-2b96/envs/prod/experiments.db
    ubuntu-intel/envs/prod/experiments.db
    amd-workstation/envs/prod/experiments.db
```

For cross-platform analysis, attach all databases in a single SQLite
session:

```bash
sqlite3 /shared/alems-data/gn100-2b96/envs/prod/experiments.db << 'SQL'
ATTACH '/shared/alems-data/ubuntu-intel/envs/prod/experiments.db' AS intel;
ATTACH '/shared/alems-data/amd-workstation/envs/prod/experiments.db' AS amd;

SELECT
  'grace' as platform, AVG(total_energy_uj) as avg_uj
FROM runs
WHERE workflow_type = 'linear'
UNION ALL
SELECT
  'intel', AVG(total_energy_uj)
FROM intel.runs
WHERE workflow_type = 'linear'
UNION ALL
SELECT
  'amd', AVG(total_energy_uj)
FROM amd.runs
WHERE workflow_type = 'linear';
SQL
```

---

## Documentation Deployment

After any change to platform documentation, deploy to GitHub Pages:

```bash
cd ~/mydrive/alems-platform
git add -A
git commit -m "docs: description of change"
bash scripts/build-docs.sh --deploy
```

`--deploy` pushes source to `main` and documentation to `gh-pages`.
Both happen together. See
[Contributing Documentation](../contributing/adding-documentation.md)
for the full documentation protocol.

## Serving Engine Configuration

Each serving engine plugin requires two env vars per engine in
`~/.alemsrc` on every machine that runs experiments against that engine.
These are the only machine-specific configuration A-LEMS requires for
serving engine telemetry.

### The two URL variables

Every engine has exactly two variables:

```
ALEMS_<ENGINE>_API_URL     — OpenAI-compatible inference base URL.
                              Includes /v1 suffix.
                              Used by models_loader as provider base_url.
                              Used by the inference client for chat completions.

ALEMS_<ENGINE>_ENGINE_URL  — Serving engine root URL.
                              No /v1 suffix, no trailing slash.
                              Used by the adapter for telemetry probes
                              (/metrics, /health, /v1/models).
```

These are different variables pointing to the same host and port but
serving different purposes.
Never alias them, never strip `/v1` in code — the distinction is
intentional.

### Complete ~/.alemsrc reference for GN100

```bash
export ALEMS_DATA_ROOT=/mnt/alems-data
export ALEMS_MODELS_DIR=/home/dpani/mydrive/models

# Inference API base URLs (OpenAI-compat, include /v1)
export ALEMS_VLLM_API_URL=http://100.84.85.2:8000/v1
export ALEMS_SGLANG_API_URL=http://100.84.85.2:30000/v1
export ALEMS_LLAMA_CPP_API_URL=http://100.84.85.2:8080/v1
export ALEMS_COLIBRI_API_URL=http://100.84.85.2:8001/v1
export ALEMS_TRT_LLM_API_URL=http://100.84.85.2:8003/v1

# Serving engine root URLs (no /v1, for adapter telemetry probes)
export ALEMS_VLLM_ENGINE_URL=http://100.84.85.2:8000
export ALEMS_SGLANG_ENGINE_URL=http://100.84.85.2:30000
export ALEMS_LLAMA_CPP_ENGINE_URL=http://100.84.85.2:8080
export ALEMS_COLIBRI_ENGINE_URL=http://100.84.85.2:8001
export ALEMS_TRT_LLM_ENGINE_URL=http://100.84.85.2:8003
```

Replace the IP address with the machine's LAN address.
Port assignments are lab convention — do not change them across machines
or experiment YAML files will need updating.

### Port assignments (lab convention)

| Engine | API port | Notes |
|---|---|---|
| vLLM | 8000 | Primary inference engine |
| SGLang | 30000 | SGLang default |
| llama.cpp | 8080 | llama-server or llama-cpp-python |
| Colibri | 8001 | MoE specialist engine |
| TRT-LLM | 8003 | NGC container, mapped to 8003 externally |

### Adding a new machine

When a new machine joins the lab fleet:

1. Install A-LEMS core and plugins per the installation guide.
2. Create `~/.alemsrc` with the machine's IP in place of `100.84.85.2`.
3. Source it: `source ~/.alemsrc`
4. Add `source ~/.alemsrc` to `~/.bashrc` so it persists across sessions.
5. Verify fragment discovery:

```bash
venv/bin/python3 -c "
from importlib.metadata import entry_points
for g in ['alems.engines.serving','alems.models.fragments','alems.preflight.checks']:
    print(g, '->', [ep.name for ep in entry_points(group=g)])
"
```

All three groups must show all five engine names.
If a group is empty, the plugin was not installed via `venv/bin/pip`.
Never use system pip for A-LEMS plugins — `alems-platform` is only
visible inside the venv.
