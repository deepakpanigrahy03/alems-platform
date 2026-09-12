# Database Path Resolution

A-LEMS resolves the database path through four layers in strict priority order.
Understanding this chain is the first step in debugging any "database not found"
or "wrong database" problem.

---

## Resolution Chain

```
Layer 0:  .alems-env in checkout root          (highest priority)
Layer 1:  ALEMS_DATA_ROOT env var + hostname
Layer 2:  app_settings.yaml database path
Layer 3:  data/experiments.db in project root  (fallback)
```

`path_loader.py` self-sources `~/.alemsrc` before reading any environment
variable. You do not need to source it manually before calling any A-LEMS
script. This is the Ab Initio pattern: every script is self-contained.

---

## Layer 0: .alems-env (checkout level)

The file `.alems-env` in the project root controls environment and optionally
overrides the data root. It is created by `install.sh` and is gitignored.

Supported formats:

```
# Key=value format (current)
ALEMS_ENV=prod
ALEMS_DATA_ROOT=/override/path

# Legacy single-token format (still supported)
prod
```

Valid `ALEMS_ENV` values: `dev`, `integration`, `preprod`, `prod`.

**Path formula by environment:**

```
prod:  $ALEMS_DATA_ROOT/<hostname>/envs/prod/experiments.db
dev:   $ALEMS_DATA_ROOT/<hostname>/envs/<user>/dev/<project>/experiments.db
```

On GN100 with `ALEMS_ENV=prod` and `ALEMS_DATA_ROOT=/mnt/alems-data`:

```
/mnt/alems-data/gn100-2b96/envs/prod/experiments.db
```

---

## Layer 1: ALEMS_DATA_ROOT + hostname

If `.alems-env` is absent or has no `ALEMS_DATA_ROOT`, the resolver reads
`ALEMS_DATA_ROOT` from the environment (sourced from `~/.alemsrc`).

Without a `.alems-env` environment selection:

```
$ALEMS_DATA_ROOT/<hostname>/experiments.db
```

---

## Layer 2: app_settings.yaml

If `ALEMS_DATA_ROOT` is not set anywhere, the resolver reads
`database.sqlite.db_name` from `config/app_settings.yaml` and resolves
it relative to the project root.

---

## Layer 3: Hardcoded fallback

If all layers fail, the path is `data/experiments.db` in the project root.
This only applies to fresh checkouts before `install.sh` has run.

---

## Debug Commands

**Print the resolved DB path:**

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate && \
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
"
```

**Check what .alems-env contains:**

```bash
cat ~/mydrive/alems-platform/.alems-env
```

**Check what ~/.alemsrc sets:**

```bash
grep -E "ALEMS_DATA_ROOT|ALEMS_ENV" ~/.alemsrc
```

**Verify the DB file exists and is readable:**

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '/home/dpani/mydrive/alems-platform')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")
ls -lh "$DB"
sqlite3 "$DB" "SELECT COUNT(*) FROM runs;" 2>/dev/null
```

**Check schema version:**

```bash
sqlite3 "$DB" \
  "SELECT MAX(version) FROM migration_history \
   WHERE type='schema' AND status='applied' AND version < 9000;"
```

**List all tables:**

```bash
sqlite3 "$DB" ".tables"
```

**Check runs table column count:**

```bash
sqlite3 "$DB" "pragma table_info(runs);" | wc -l
```

---

## Common Problems

**`ALEMS_DATA_ROOT is not set`**

`~/.alemsrc` was not sourced and `ALEMS_DATA_ROOT` is not in the environment.

```bash
source ~/.alemsrc
```

If `~/.alemsrc` does not exist, re-run `bash scripts/install.sh`.

**Wrong database (wrong machine or wrong environment)**

Check which environment `.alems-env` selects and whether `ALEMS_DATA_ROOT`
points to the right mount:

```bash
cat ~/mydrive/alems-platform/.alems-env
echo $ALEMS_DATA_ROOT
```

**Database locked**

Another process is writing. Find it:

```bash
fuser "$DB"
```

**Schema mismatch**

Run pending migrations:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
alems dev sync
```

---

## Path Configuration Object

For scripts that need multiple paths (docs build, report engine, diagram
generator), use `PathConfig` from `path_loader`:

```python
from scripts.tools.path_loader import PathConfig
cfg = PathConfig()
print(cfg.DB_PATH)
print(cfg.MKDOCS_SOURCE)
print(cfg.DIAGRAMS_OUTPUT)
```

`PathConfig` reads `config/paths.yaml` for doc and tool paths, and calls
`get_alems_db_path()` for the database path.
