---
topic: "Quick Start"
audience: user
status: current
last_validated: 2026-09-11
---

# Quick Start

This guide assumes you have completed [Installation](01-installation.md) and configured at least one provider in [API Keys](03-model-config.md). `alems dev status` should show a `✓` next to at least one provider before continuing.

---

## Your First Experiment

```bash
alems run gsm8k_basic
```

`gsm8k_basic` runs a set of grade school math problems against your default provider, measuring energy from first token request to last token received. It runs a linear workload (single LLM call) and an agentic workload (planning, tool calls, synthesis) side by side, so the output includes both.

A run takes 2 to 5 minutes depending on your hardware and provider latency. The terminal shows a progress line for each inference call.

---

## Reading the Output

When the run completes, A-LEMS prints a summary:

```
Run complete
  run_id (linear):   1841
  run_id (agentic):  1842
  provider:          groq / llama-3.1-8b-instruct
  task:              gsm8k_basic
  platform:          nvidia_grace / direct

  Energy
    linear total:    4,821 µJ   (4.8 mJ)
    agentic total:   9,347 µJ   (9.3 mJ)
    orchestration:   1,203 µJ   (12.9% of agentic)

  Compute
    instructions:    8.2 billion
    IPC:             2.41
    wall time:       3.4 s

  Measurement tier:  direct (SPBM + ARM PMU)
  Baseline:          baseline_20260911_143201
```

**Energy** is in microjoules (µJ). 1,000 µJ = 1 mJ = 0.001 J. The orchestration line shows how much of the agentic run's energy went to planning and coordination overhead rather than direct inference, expressed as a percentage of total agentic energy. This is the orchestration tax.

**Measurement tier** confirms what the energy values represent. `direct` means the values come from hardware counter reads (SPBM, RAPL, or IOKit). `modeled` means `energy_uj = 0` because your platform does not expose hardware energy counters.

**Baseline** is the idle measurement subtracted before reporting energy values. You can find it in `alems dev status`.

---

## Where Results Are Stored

Results go into your experiment database. The path is shown in `alems dev status`:

```
Database: /mnt/alems-data/gn100-2b96/envs/dpani/dev/alems-platform/experiments.db
```

Query results directly:

```bash
sqlite3 $ALEMS_DATA_ROOT/$(hostname)/envs/$USER/dev/alems-platform/experiments.db \
  "SELECT run_id, task_id, total_energy_uj, workflow_type FROM runs ORDER BY run_id DESC LIMIT 10;"
```

The GUI provides a visual interface for browsing runs, comparing energy across providers, and generating reports. Start it with:

```bash
python gui/app.py
```

---

## Running More Tasks

List available tasks:

```bash
alems run --list
```

Tasks include single-turn benchmarks (gsm8k, MMLU), tool-use workloads, and multi-step agentic tasks. Each task definition lives in `config/tasks/` as a YAML file.

To run against a specific provider:

```bash
alems run gsm8k_basic --provider groq
alems run gsm8k_basic --provider vllm_remote
alems run gsm8k_basic --provider openai --model gpt-4o
```

---

## If a Run Fails

The most common failure causes are:

**Provider unreachable** — check `alems dev status` for provider connectivity. For vllm, confirm the server is running at `ALEMS_VLLM_REMOTE_URL`.

**Energy reader error** — on NVIDIA Grace, confirm DCGM is running: `sudo systemctl status nvidia-dcgm`. On Intel and AMD, confirm RAPL is accessible: `ls /sys/class/powercap/intel-rapl/`.

**Database locked** — another A-LEMS process is writing to the database. Wait for it to finish or check with `ps aux | grep alems`.

**Schema mismatch** — run `alems dev sync` to apply any pending migrations.

For hardware-specific failures, see [Troubleshooting](05-troubleshooting.md).
