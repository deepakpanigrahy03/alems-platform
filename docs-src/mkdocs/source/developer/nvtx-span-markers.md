
## Phase Markers for NVIDIA Profilers (NVTX)

A-LEMS spans (run, orchestration, inference, tool, retry) can be shown on the timelines of NVIDIA Nsight Systems and Nsight Compute as NVTX ranges, so GPU kernels can be attributed to agent phases.
Core forwards every span open and close to listener plugins (entry point group `alems.spans.listeners`); the NVTX listener is the `alems-plugin-nvtx` package.
It is an A-LEMS level setting, `plugins.nvtx` in `config/app_settings.yaml` (`enabled: false`, `domain: alems`), switched on for one profiled run with `ALEMS_NVTX_ENABLED=1` (`~/.alemsrc` or shell); `ALEMS_NVTX_DOMAIN` changes the domain. Core resolves these and passes them to the listener. With it off, or without the `nvtx` Python package, nothing is called and nothing changes.

Each range is named `<kind>:<name> [<span_id>]` in the configured domain.

**Profiling a run and checking agreement:**
```bash
export ALEMS_NVTX_ENABLED=1
nsys profile --trace=cuda,nvtx --output run_report alems run <profile>
nsys export --type sqlite --output run_report.sqlite run_report.nsys-rep
python3 scripts/tools/check_nvtx_spans.py --nsys run_report.sqlite --store <store.db> --run-id <run_id> --write
```
The checker matches each range to its stored span by span id and compares durations and start offsets from the run's root span (the two tools have different clock zero points, so absolute times are not compared). Tolerance: `--tol-ms` or `ALEMS_NVTX_TOL_MS` (default 1 ms).

<a id="nvtx-span-agreement"></a>
**Stored result (method `nvtx_span_agreement_v1`).** With `--write` the checker stores one row in `energy_validation_nvtx_agreement`: run_id, report path, report sha256, nsys version, spans stored, ranges found, matched, missing, outside tolerance, largest start offset and duration difference (ms), tolerance, status (`agree`, `disagree`, `no_ranges`, `trace_missing`) and check time. Without `--write` both files are only read.

**Limitations.** Ranges are emitted in the A-LEMS process. Kernels of a separate serving engine (vLLM, SGLang) run in the server process; to see both, profile the server and the client together, and associate kernels with phases by time. Profiling changes timing: energy is never taken from a profiled run.
