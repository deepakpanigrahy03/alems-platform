# Installation

A-LEMS installs on 8 platform classes across 4 ISAs. The installer detects
your hardware automatically and configures everything for your machine. It
asks two questions. Everything else is automatic.

---

## Prerequisites

The installer checks prerequisites before touching your system and stops with
a clear error message if anything is missing.

**All Linux platforms require:**

```bash
# Debian / Ubuntu
sudo apt install -y build-essential python3-dev python3-venv sqlite3

# Fedora / RHEL
sudo dnf install -y gcc gcc-c++ python3-devel sqlite

# Arch
sudo pacman -S --noconfirm base-devel python sqlite
```

Python 3.9 to 3.13 is required. Python 3.14+ is not yet supported.

**Per-platform additional requirements:**

| Platform | Required | Optional |
|---|---|---|
| `nvidia_grace` | nvidia-smi, perf, dcgmi | none |
| `intel_x86` | perf | turbostat, rdmsr |
| `amd_x86` | perf | rdmsr |
| `apple_silicon` | Homebrew, powermetrics | none |
| `linux_arm` | perf | none |
| `linux_x86_unknown` | perf | rdmsr |

**NVIDIA Grace (GN100, DGX Spark):** install DCGM before running the installer:

```bash
sudo apt install datacenter-gpu-manager
sudo systemctl enable nvidia-dcgm && sudo systemctl start nvidia-dcgm
```

**NVIDIA Grace with Secure Boot enabled:** SPBM hwmon (CPU and system energy)
is unavailable because it requires an unsigned kernel module. GPU energy via
DCGM still works. Disable Secure Boot in BIOS to enable full energy measurement.

**Apple Silicon:** install Xcode CLI tools if not present:

```bash
xcode-select --install
```

---

## Install

```bash
git clone https://github.com/deepakpanigrahy03/alems-platform.git
cd alems-platform
bash scripts/install.sh
```

The installer prints its progress across 12 steps. It asks two questions
during Step 5. Everything else runs without input.

**Question 1: Environment**

```
Environment [dev]: 
```

Enter `dev` for a development or research machine (recommended for first
installs). Enter `prod` for a shared measurement server. Dev installs isolate
your database by username and project name. Prod installs use a shared path.
The installer suggests `dev` when you are on the `main` branch.

**Question 2: Data root**

```
Data root [/mnt/alems-data]: 
```

Enter the directory where A-LEMS stores databases, baselines, and measurement
files. This can be on any mounted drive. The installer creates it if it does
not exist.

After install completes:

```bash
source ~/.bashrc          # or source ~/.zshrc on Mac
alems dev status
```

---

## What the Installer Does

**Step 0** runs a fast hardware probe using `detect_hardware.py` to get your
`platform_class`. All downstream steps key off `platform_class`, not OS or
architecture strings. Adding a new platform to `detect_hardware.py` makes it
work here automatically.

**Step 0.5** checks prerequisites for your detected platform. Required tools
that are missing cause the installer to exit immediately with the exact install
command to fix each one.

**Step 1** installs system build dependencies before creating the Python
environment. This ordering matters: `psutil` and other packages require
`python3-dev` and `gcc` at compile time.

**Step 1b** creates a Python virtual environment in `venv/`. If `venv/`
already exists it is reused without reinstalling.

**Step 2** installs all Python dependencies into the venv, including
platform-specific packages (Metal-enabled `llama-cpp-python` on Apple Silicon,
DCGM bindings on NVIDIA Grace).

**Step 3** sets file permissions for hardware counter access. `sudo` is used
once here via `fix_permissions.sh`. It is not required again after install.

**Step 4** runs full hardware detection and verification. On direct energy
platforms this confirms that RAPL, SPBM, or IOKit counters are readable. If
verification fails the installer stops.

**Step 5** writes two configuration files based on your answers:

```
.alems-env          project root, gitignored — stores ALEMS_ENV
~/.alemsrc          home directory — stores ALEMS_DATA_ROOT and API keys
```

Neither file is ever committed to the repository.

**Step 6** initializes the SQLite database and applies all schema migrations
in version order.

**Steps 7 through 11** seed reference data: measurement methodology entries,
platform configuration, model parameters, quality thresholds, and the display
registry.

**Step 12** measures an idle energy baseline on direct energy platforms. The
baseline captures background power consumption and is subtracted from every
experiment run automatically. Modeled platforms skip this step.

---

## Supported Platforms

| Platform | Hardware | Energy | Compute |
|---|---|---|---|
| `nvidia_grace` | GN100, DGX Spark | Direct (SPBM) or Modeled (Secure Boot) | Direct (ARM PMU) |
| `intel_x86` | Any Intel bare metal | Direct (RAPL) | Direct (RAPL + PMU) |
| `amd_x86` | Any AMD bare metal | Direct (RAPL) | Direct (RAPL + PMU) |
| `apple_silicon` | M1 / M2 / M3 / M4 | Direct (IOKit) | Direct (kperf PMU) |
| `linux_arm` | Graviton, RPi, KVM VMs | Modeled | Direct (ARM PMU) |
| `linux_x86_unknown` | VMs, Hygon, unknown x86 | Modeled | Estimated |
| `intel_mac` | Pre-2020 Intel Mac | Unavailable | Estimated |
| `linux_riscv` | SiFive, StarFive | Unavailable | Estimated |

Direct reads a hardware counter. Modeled records `energy_uj = 0` with an
explicit measurement tier. The tier for your machine appears in every run
record and in `alems dev status`.

---

## Verify

```bash
alems dev status
```

Expected output:

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

If `Platform: unknown` appears, run:

```bash
cd alems-platform && source venv/bin/activate
python3 scripts/detect_hardware.py --stdout
```

For other errors see [Troubleshooting](05-troubleshooting.md).

---

## Post-Install Verification

After `alems dev status` shows a clean environment, run these checks to
confirm the database is fully seeded and the baseline was captured.

**Schema version:**

```bash
DB=$(python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.tools.path_loader import get_alems_db_path
print(get_alems_db_path())
")

sqlite3 "$DB" "
SELECT version, type, status, applied_at
FROM migration_history
WHERE status='applied'
ORDER BY version DESC LIMIT 5;
"
```

Expected: schema at v086 and v9000 (baseline adoption marker) both applied.

**Seed data loaded:**

```bash
sqlite3 "$DB" "SELECT COUNT(*) FROM energy_sources;"        # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM energy_domains;"        # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM measurement_methodology;"  # expect 25
sqlite3 "$DB" "SELECT COUNT(*) FROM task_categories;"       # expect 65
sqlite3 "$DB" "SELECT COUNT(*) FROM metric_display_registry;"  # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM normalization_factors;" # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM outlier_detection_config;" # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM retry_policy;"          # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM power_limits;"          # expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM analysis_domain_config;" # expect > 0
```

**Idle baseline captured** (direct energy platforms only):

```bash
sqlite3 "$DB" "
SELECT
  baseline_id,
  round(package_power_watts, 3) as pkg_w,
  round(gpu_power_watts, 3)     as gpu_w,
  duration_seconds,
  sample_count,
  governor,
  method
FROM idle_baselines
ORDER BY rowid DESC LIMIT 1;
"
```

A healthy baseline shows non-zero `package_power_watts`, `duration_seconds`
of 90 (3-minute measurement), `sample_count` of around 3, and `method` of
`idle_measurement`. On NVIDIA Grace, `gpu_power_watts` is populated via SPBM.

On modeled platforms (linux_arm, linux_x86_unknown) the baseline step is
skipped and this table is empty. That is expected.

**Platform seed data loaded:**

```bash
sqlite3 "$DB" "SELECT COUNT(*) FROM power_rails;"   # nvidia_grace only, expect > 0
sqlite3 "$DB" "SELECT COUNT(*) FROM thermal_zones;" # expect > 0
```

**Migration system status:**

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/tools/alems_migrate.py --status
```

Shows every applied migration with version, type, checksum, and timestamp.
All entries should show `applied`. Any `pending` entry means a migration
did not run — fix with `alems dev sync`.

---

## Migration System

A-LEMS uses a checksum-enforced migration runner (`alems_migrate.py`).
Migrations live in three folders:

```
migrations/
  schema/     — DDL changes: v049 through v086, v9000 (baseline adoption)
  seed/       — Reference data: s001 through s008
  platform/   — Platform-specific seed: gn100, nvidia_grace, intel_x86, amd_x86, apple_m1
```

In `prod` environment, any modification to an applied migration file causes
a FATAL error at next sync. In `dev` environment, checksum mismatches are
healed with a warning.

To check migration status without applying anything:

```bash
python3 scripts/tools/alems_migrate.py --check
python3 scripts/tools/alems_migrate.py --plan    # dry run
```
