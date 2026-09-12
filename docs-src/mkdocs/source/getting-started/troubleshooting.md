# Troubleshooting

Common problems and verified solutions. Every command on this page
has been tested on a live A-LEMS installation.

---

## Quick Reference

| Problem | Cause | Fix |
|---|---|---|
| `ALEMS_DATA_ROOT not set` | `~/.alemsrc` not sourced | `source ~/.alemsrc` |
| `Database not found` | Wrong path or unmounted drive | Check `cat .alems-env` and `echo $ALEMS_DATA_ROOT` |
| `Schema version mismatch` | Pending migrations | `alems dev sync` or `python3 scripts/tools/alems_migrate.py` |
| `Platform: unknown` | `hw_config.json` missing or stale | `python3 scripts/detect_hardware.py` |
| `capability_profile: {}` | Old v3 hw_config.json | `python3 scripts/detect_hardware.py` |
| `Permission denied` on hardware | Permissions not set | `sudo bash scripts/fix_permissions.sh` |
| `ModuleNotFoundError` | venv not activated | `source venv/bin/activate` |
| `Database locked` | Another process writing | `fuser $DB` to find it |
| Provider shows `✗` in status | Key missing or server down | See [API Keys](api-keys.md) |

---

## Environment Problems

### ALEMS_DATA_ROOT not set

```bash
source ~/.alemsrc
bash scripts/alems dev status
```

If `~/.alemsrc` does not exist, re-run the installer:

```bash
bash scripts/install.sh
```

### Wrong database being used

Print the resolved path:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
"
```

Check what environment is selected:

```bash
cat ~/mydrive/alems-platform/.alems-env
echo $ALEMS_DATA_ROOT
```

### Schema version mismatch

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/tools/alems_migrate.py --status
python3 scripts/tools/alems_migrate.py
```

---

## Hardware Detection Problems

### Platform shows as unknown

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/detect_hardware.py
python3 -c "
import json
with open('config/hw_config.json') as f:
    cfg = json.load(f)
print('platform_class:', cfg.get('platform_class'))
print('hw_config_version:', cfg.get('hw_config_version'))
"
```

### capability_profile shows empty {}

The `hw_config.json` is an old version 3 file. Regenerate:

```bash
python3 scripts/detect_hardware.py
```

Version 4 adds `capability_profile`, `energy_measurement`, and
`compute_measurement` as top-level keys.

### Permission denied on hardware counters

```bash
sudo bash scripts/fix_permissions.sh
```

Log out and log back in for group permission changes to take effect.
The installer runs this automatically at Step 3 — run it manually
only if permissions were changed after install.

### RAPL not accessible on Intel/AMD

```bash
# Check RAPL sysfs paths exist
ls /sys/class/powercap/intel-rapl*/energy_uj 2>/dev/null

# Check read permission
cat /sys/class/powercap/intel-rapl/energy_uj
```

If the path exists but returns `Permission denied`, run
`fix_permissions.sh`. If the path does not exist, RAPL is not
supported on this CPU or kernel.

### SPBM not accessible on NVIDIA Grace

SPBM hwmon requires Secure Boot to be disabled. If Secure Boot is
enabled, SPBM channels are unavailable and `energy_measurement` will
show `modeled`. DCGM GPU energy is still available.

Check Secure Boot status:

```bash
mokutil --sb-state
```

---

## Database Problems

### Database locked

Another A-LEMS process is writing. Find it:

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")
fuser "$DB"
```

Wait for the process to finish. Do not delete WAL or journal files
while a process is running — this corrupts the database.

### Database file not found

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")
ls -lh "$DB"
```

If the file does not exist, the data root directory may not be
mounted. Check:

```bash
ls $ALEMS_DATA_ROOT
mount | grep alems
```

---

## Provider Problems

### Provider shows ✗ in alems dev status

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate

# Check keys are set
grep -E "GROQ|OPENAI|ANTHROPIC|NVIDIA|GEMINI|VLLM" ~/.alemsrc

# Reload environment
source ~/.alemsrc

# Test specific provider
python3 core/execution/tests/test_llm_setup.py --provider groq --verbose
```

### vllm server unreachable

```bash
echo $ALEMS_VLLM_REMOTE_URL
curl -s --connect-timeout 3 "${ALEMS_VLLM_REMOTE_URL}/models" | \
  python3 -m json.tool | head -10
```

If the curl fails, the vllm server is not running or not reachable
at the configured URL.

---

## Build and Docs Problems

### mkdocs build warnings

```bash
cd ~/mydrive/alems-platform
bash scripts/build-docs.sh --validate-only
```

Fix any methodology reference failures first, then fix nav warnings.

### Graphviz not installed

```bash
sudo apt install -y graphviz
```

Required for `generate_diagrams.py` to produce SVG diagrams.

---

## Getting Help

Run the full environment status check:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
bash scripts/alems dev status
python3 scripts/detect_hardware.py
python3 scripts/tools/validate_methodology_refs.py
```

Include the output of these three commands when reporting an issue
on GitHub at `https://github.com/deepakpanigrahy03/alems-platform/issues`.
