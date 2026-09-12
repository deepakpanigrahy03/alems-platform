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
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

BACKUP_DIR="$ALEMS_DATA_ROOT/$(hostname)/backups"
mkdir -p "$BACKUP_DIR"

sqlite3 "$DB" ".backup $BACKUP_DIR/experiments_$(date +%Y%m%d_%H%M%S).db"
echo "Backup complete: $BACKUP_DIR"
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

After any hardware change, re-measure the idle baseline:

```bash
bash scripts/alems measure
```

The new baseline replaces the old one in the database. All future
runs subtract the new baseline.

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
