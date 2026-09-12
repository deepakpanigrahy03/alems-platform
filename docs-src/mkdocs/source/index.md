# A-LEMS

**A-LEMS** is a research-grade measurement and profiling framework for AI workloads.
It captures telemetry across hardware, system, orchestration, and workload levels,
enabling energy-aware AI systems research and rigorous evaluation of model behavior
across heterogeneous hardware.

[![GitHub](https://img.shields.io/badge/GitHub-alems--platform-blue)](https://github.com/deepakpanigrahy03/alems-platform)
[![Python](https://img.shields.io/badge/Python-3.9%2B-green)](https://python.org)
[![Platforms](https://img.shields.io/badge/Platforms-10-orange)](getting-started/installation.md)
[![License](https://img.shields.io/badge/License-Apache%202.0-lightgrey)](https://github.com/deepakpanigrahy03/alems-platform/blob/main/LICENSE)

---

## What A-LEMS Measures

| Level | Metrics |
|---|---|
| Hardware | CPU package, core, uncore, DRAM energy (RAPL / SPBM / IOKit) |
| Performance | Instructions, cycles, IPC, L1/L2/L3 cache activity, ARM PMU |
| System | Context switches, interrupts, memory faults, disk I/O |
| Thermal | Temperature, fan RPM, voltage, cooling device state |
| Network | Bytes sent and received per inference call |
| Workload | Prompt tokens, completion tokens, TTFT, TPOT, wall time |
| Orchestration | Planning, tool execution, synthesis phases with per-phase energy |

153 columns per run combining hardware, system, network, LLM, and orchestration metrics.
Every value carries a provenance record naming the hardware counter, method, layer,
and confidence level that produced it.

---

## Supported Hardware

A-LEMS reads hardware counters directly on 8 platform classes across 4 ISAs.

| Platform | Hardware | Energy Source |
|---|---|---|
| `nvidia_grace` | GN100, DGX Spark | SPBM + DCGM + ARM PMU |
| `intel_x86` | Any Intel bare metal | RAPL + MSR + turbostat |
| `amd_x86` | Any AMD bare metal | RAPL + MSR |
| `apple_silicon` | M1 / M2 / M3 / M4 | IOKit + powermetrics |
| `linux_arm` | Graviton, RPi, KVM VMs | ARM PMU (energy modeled) |
| `linux_x86_unknown` | VMs, Hygon, unknown x86 | RAPL if exposed |
| `intel_mac` | Pre-2020 Intel Mac | observation only |
| `linux_riscv` | SiFive, StarFive | observation only |

---

## Who Uses A-LEMS

| Audience | Purpose |
|---|---|
| Researchers | Measure and compare LLM inference energy across hardware and providers |
| Silicon developers | Analyze energy and thermal behavior at the hardware counter level |
| ML engineers | Capture per-run telemetry for model and serving engine optimization |
| Orchestration teams | Quantify the energy overhead of agentic coordination |

---

## Providers and Tasks

A-LEMS runs experiments against 16 providers including vllm (local and remote),
Groq, OpenAI, Anthropic, Google Gemini, NVIDIA NIM, Ollama, llama.cpp, and
speech providers (Kokoro TTS, Indic Parler, Faster Whisper STT).

65 task types span arithmetic reasoning, code generation, question answering,
summarization, named entity extraction, tool-chain execution, multi-step agentic
tasks, text-to-speech, speech-to-text, and voice cloning.

---

## Documentation

| Section | For |
|---|---|
| [Getting Started](getting-started/installation.md) | Install, configure, run first experiment |
| [Concepts](concepts/measurement-model.md) | Understand what A-LEMS measures and how |
| [Developer](developer/architecture.md) | Add platforms, readers, providers, tasks |
| [Reference](reference/platform-matrix.md) | Platform matrix, provider registry, CLI, DB paths |
| [Research](research/measurement-methodology.md) | Methodology, attribution, citations |
| [Guides](guides/researcher-journey.md) | End-to-end journeys through the platform |

---

## Papers

A-LEMS measurements appear in research on orchestration tax, energy attribution,
and cross-platform LLM inference efficiency.
See [Publications](research/publications.md) for the full list.

---

*Apache License 2.0.
The earlier research prototype lives at
[deepakpanigrahy03/a-lems](https://github.com/deepakpanigrahy03/a-lems).*
