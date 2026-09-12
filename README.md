<div align="center">

# ⚡ A-LEMS
### **Agentic LLM Energy Measurement System**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge&logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-Apache%202.0-green?style=for-the-badge)](LICENSE)
[![Platforms](https://img.shields.io/badge/Platforms-10-orange?style=for-the-badge)](https://deepakpanigrahy03.github.io/alems-platform/getting-started/installation/)
[![Docs](https://img.shields.io/badge/Docs-live-brightgreen?style=for-the-badge)](https://deepakpanigrahy03.github.io/alems-platform/)

**Research-grade measurement and profiling framework for AI workloads**

<a href="https://deepakpanigrahy03.github.io/alems-platform/" target="_blank">📖 Documentation</a> •
<a href="https://deepakpanigrahy03.github.io/alems-platform/research/measurement-methodology/" target="_blank">🔬 Methodology</a> •
<a href="https://deepakpanigrahy03.github.io/alems-platform/research/publications/" target="_blank">📄 Publications</a>

</div>

---

## What A-LEMS Does

A-LEMS captures telemetry across hardware, system, orchestration, and workload
levels for LLM inference workloads. It reads hardware counters directly — RAPL,
SPBM, IOKit, ARM PMU, DCGM — with no estimation, and records 153 columns per
run with full provenance tracing every value to the hardware counter that
produced it.

| **Linear Workload** | **Agentic Workload** |
|---|---|
| Single LLM call | Planning + Tool calls + Synthesis |
| Baseline energy cost | Baseline + orchestration tax |

The **orchestration tax** is the energy overhead of agentic coordination.
A-LEMS measures it directly, per phase, per run, per platform.

---

## Key Capabilities

| Level | What Is Measured |
|---|---|
| Hardware | CPU package, core, uncore, DRAM energy (RAPL / SPBM / IOKit) |
| Performance | Instructions, cycles, IPC, L1/L2/L3 cache, ARM PMU |
| System | Context switches, interrupts, memory faults, disk I/O |
| Thermal | Temperature, fan RPM, voltage, cooling device state |
| Workload | Prompt tokens, completion tokens, TTFT, TPOT, wall time |
| Orchestration | Planning, execution, synthesis phases with per-phase energy |

---

## Supported Platforms

| Platform | Hardware | Energy Stack | Tier |
|---|---|---|---|
| `nvidia_grace` | GN100, DGX Spark | SPBM + DCGM + ARM PMU | Direct |
| `intel_x86` | Any Intel bare metal | RAPL + MSR + turbostat | Direct |
| `amd_x86` | Any AMD bare metal | RAPL + MSR | Direct |
| `apple_silicon` | M1 / M2 / M3 / M4 | IOKit + powermetrics | Direct |
| `linux_arm` | Graviton, RPi, KVM VMs | ARM PMU | Modeled |
| `linux_x86_unknown` | VMs, Hygon, unknown x86 | RAPL if exposed | Modeled |
| `intel_mac` | Pre-2020 Intel Mac | observation only | Unavailable |
| `linux_riscv` | SiFive, StarFive | observation only | Unavailable |

---

## Providers

16 providers: vllm (local and remote), Groq, OpenAI, Anthropic, Google Gemini,
NVIDIA NIM, Ollama, llama.cpp, DeepSeek, Kokoro TTS, Indic Parler TTS,
IndicF5 voice clone, Faster Whisper STT.

---

## Install

```bash
git clone https://github.com/deepakpanigrahy03/alems-platform.git
cd alems-platform
bash scripts/install.sh
source ~/.bashrc
alems dev status
```

The installer detects your platform automatically and asks two questions:
environment (dev or prod) and data root path.

---

## Run an Experiment

```bash
cd alems-platform && source venv/bin/activate
python3 core/execution/tests/run_experiment.py \
  --tasks gsm8k_basic \
  --repetitions 1 \
  --provider groq \
  --workflow-mode comparison \
  --experiment-type normal \
  --experiment-goal "first experiment" \
  --save-db
```

List all 65 available tasks:

```bash
python3 core/execution/tests/run_experiment.py --list-tasks
```

---

## Documentation

**[deepakpanigrahy03.github.io/alems-platform](https://deepakpanigrahy03.github.io/alems-platform/)**

- [Installation](https://deepakpanigrahy03.github.io/alems-platform/getting-started/installation/) — prerequisites, install steps, platform verification
- [Quick Start](https://deepakpanigrahy03.github.io/alems-platform/getting-started/quick-start/) — first experiment, reading output
- [Concepts](https://deepakpanigrahy03.github.io/alems-platform/concepts/measurement-model/) — measurement model, platform detection, energy tiers
- [Developer Guide](https://deepakpanigrahy03.github.io/alems-platform/developer/architecture/) — adding platforms, readers, providers
- [Research Methodology](https://deepakpanigrahy03.github.io/alems-platform/research/measurement-methodology/) — per-platform measurement, citation templates

---

## Citation

```bibtex
@software{panigrahy2026alems,
  title   = {A-LEMS: Agentic LLM Energy Measurement System},
  author  = {Panigrahy, Deepak},
  year    = {2026},
  url     = {https://github.com/deepakpanigrahy03/alems-platform}
}
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE)
