---
topic: "Installation"
audience: user
status: current
last_validated: 2026-09-11
---

# Installation

A-LEMS installs on 8 platform configurations across 4 ISAs. The installer detects your hardware automatically and configures everything for your machine. You answer two questions. The rest is automatic.

---

## Prerequisites

Check these before running the installer. The installer will stop with a clear error if anything is missing, but checking first saves time.

| Platform | Required | Optional |
|---|---|---|
| `nvidia_grace` | gcc, python3-dev, sqlite3, perf, dcgmi | none |
| `intel_x86` | gcc, python3-dev, sqlite3, perf, rdmsr | turbostat |
| `amd_x86` | gcc, python3-dev, sqlite3, perf, rdmsr | none |
| `apple_silicon` | Homebrew, Xcode CLI tools | none |
| `linux_arm` | gcc, python3-dev, sqlite3, perf | none |
| `linux_x86_unknown` | gcc, python3-dev, sqlite3 | perf |

On Debian and Ubuntu systems, install required packages with:

```bash
sudo apt install gcc python3-dev sqlite3 linux-tools-common linux-tools-$(uname -r)
```

On NVIDIA Grace (GN100, DGX Spark), install DCGM before running the installer:

```bash
sudo apt install datacenter-gpu-manager
sudo systemctl enable nvidia-dcgm && sudo systemctl start nvidia-dcgm
```

On Apple Silicon, install Xcode CLI tools if not already present:

```bash
xcode-select --install
```

---

## Install Steps

```bash
git clone https://github.com/deepakpanigrahy03/alems-platform.git
cd alems-platform
bash scripts/install.sh
```

The installer asks two questions:

**Environment** — enter `dev` for a development install (recommended for first installs and research machines) or `prod` for a production measurement server. Dev installs isolate your database by username and project. Prod installs use a shared database path.

**Data root** — the directory where A-LEMS stores databases, baselines, and measurement files. This directory can be on any mounted drive. Example: `/mnt/alems-data` or `/home/yourname/alems-data`. The installer creates it if it does not exist.

After the installer finishes:

```bash
source ~/.bashrc          # or source ~/.zshrc on Mac
alems dev status          # confirm everything is working
```

A healthy `alems dev status` shows your platform class, environment, database path, schema version, and provider connectivity. Any missing prerequisite or misconfigured key appears here with a clear message.

---

## What the Installer Does

The installer runs 12 steps. None of them require manual intervention after the two questions are answered.

**Step 0** detects your platform by reading hardware signals: DMI table entries, CPU vendor, GPU presence, and architecture. It does not guess from `uname` output. The result is written to `hw_config.json`.

**Step 0.5** checks prerequisites for your detected platform. If a required tool is missing, the installer stops here with the exact package name to install.

**Step 1** installs system build dependencies before creating the Python environment. This ordering prevents build failures during package compilation.

**Step 2** creates a Python virtual environment and installs all Python dependencies, including platform-specific packages (llama-cpp-python with Metal on Apple Silicon, DCGM bindings on NVIDIA Grace).

**Step 3** sets file permissions that allow hardware counter reads without requiring sudo at experiment time. Sudo is used once, here, and not again.

**Step 4** runs hardware detection and verification. On direct energy platforms, this confirms that RAPL, SPBM, or IOKit counters are readable. On modeled platforms, it confirms ARM PMU access. If verification fails, the installer stops.

**Step 5** writes two configuration files based on your answers: `.alems-env` in the project root (checkout-level, gitignored) and `~/.alemsrc` in your home directory (machine-level, holds API keys and paths). Neither file is ever committed to the repository.

**Step 6** initializes the SQLite database and applies all schema migrations in version order.

**Steps 7 through 11** seed reference data: measurement methodology entries, platform configuration, model parameters, quality thresholds, and the display registry used by the GUI and reports.

**Step 12** measures an idle baseline on direct energy platforms. The baseline captures your machine's background energy consumption and is subtracted automatically from every experiment run. Modeled platforms skip this step.

---

## Supported Platforms

| Platform | Hardware Examples | Energy Measurement | Compute Measurement |
|---|---|---|---|
| `nvidia_grace` | GN100, DGX Spark | Direct (SPBM) or Modeled (Secure Boot) | Direct (ARM PMU) |
| `intel_x86` | Any Intel bare metal | Direct (RAPL) | Direct (RAPL + PMU) |
| `amd_x86` | Any AMD bare metal | Direct (RAPL) | Direct (RAPL + PMU) |
| `apple_silicon` | M1 / M2 / M3 / M4 Mac | Direct (IOKit) | Direct (kperf PMU) |
| `linux_arm` | Graviton, RPi, KVM VMs | Modeled | Direct (ARM PMU) |
| `linux_x86_unknown` | VMs, Hygon, unknown x86 | Modeled | Estimated |
| `intel_mac` | Pre-2020 Intel Mac | Unavailable | Estimated |
| `linux_riscv` | SiFive, StarFive | Unavailable | Estimated |

Direct measurement reads a hardware counter. Modeled records `energy_uj = 0` with an explicit measurement tier. Unavailable records CPU time only. The measurement tier for your machine appears in every run record and in `alems dev status`.

---

## Verifying Your Install

```bash
alems dev status
```

This shows:

```
Platform:     nvidia_grace
Environment:  dev
Database:     /mnt/alems-data/gn100-2b96/envs/dpani/dev/alems-platform/experiments.db
Schema:       v086
Branch:       main
Providers:    vllm_remote ✓   groq ✓   openai ✗   anthropic ✗
```

Providers marked `✗` are not configured. See [API Keys](03-model-config.md) to add them.

---

## Troubleshooting

If `alems dev status` shows an error, the most common causes are:

`ALEMS_DATA_ROOT not set` means `~/.bashrc` was not sourced after install. Run `source ~/.bashrc`.

`Database not found` means the data root directory does not exist or is not mounted. Check that the path you gave during install is accessible.

`Schema version mismatch` means a migration did not apply. Run `alems dev sync` to reapply.

`Platform: unknown` means `hw_config.json` is missing or unreadable. Run `python scripts/detect_hardware.py` and check the output.

For hardware-specific issues, see [Troubleshooting](05-troubleshooting.md).
