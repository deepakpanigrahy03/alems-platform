# Kernel traffic pass (Nsight Compute)

Shows which kernels read and wrote how many bytes through the GPU's L2 cache. The method is described in the research section under *Per Kernel L2 Traffic*.

## One time setup

```bash
ncu --version                                   # Nsight Compute present
grep -i profil /proc/driver/nvidia/params       # RmProfilingAdminOnly: 0 needed for a normal user
```
If it shows 1, an admin sets `options nvidia NVreg_RestrictProfilingToAdminUsers=0` in a file under `/etc/modprobe.d/`, runs `update-initramfs -u`, and reboots.

## From the command line

```bash
alems profile kernels --probe
alems profile kernels -- /path/to/venv/bin/python my_workload.py
alems profile kernels --match-run 812 -- /path/to/venv/bin/python my_workload.py
alems profile kernels --match-run 812 --match gemm --launch-count 50 -- ...
```
The interpreter is the one you write in the command; the A-LEMS venv only runs the profiler. The output is the pass id, the status, the byte totals and the agreement status.

## In a sequence plan

```yaml
treatment:
  kind: kernel_traffic
  params:
    command: "/path/to/venv/bin/python my_workload.py"
    match: "gemm"          # optional
    matched_run_id: 812    # optional reference
```
`kernel_traffic` is a profiling kind (`MEASUREMENT = "profiling"`). The sequencer calls `measure()` directly, with no measured run and no collectors, and the pass links to the attempt through `attempt_id`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `NCU` | PATH, then CUDA default | Nsight Compute binary |
| `ALEMS_KTRAFFIC_CACHE_CONTROL` | all | `all` flushes caches before each replay; `none` keeps them |
| `ALEMS_KTRAFFIC_REPLAY_MODE` | kernel | ncu replay mode |
| `ALEMS_KTRAFFIC_LAUNCH_COUNT` | none | limit of profiled launches |
| `ALEMS_KTRAFFIC_TOL` | 0.25 | diagnostic agreement band |
| `ALEMS_KTRAFFIC_TIMEOUT_S` | 3600 | pass timeout |
| `ALEMS_KTRAFFIC_PROBE_CMD` | required | command launching one GPU kernel (cudaMemset is not a kernel) |
| `ALEMS_KTRAFFIC_NAME_BASE` | demangled | name the `--match` regex sees: function, demangled or mangled |

## Queries

```sql
SELECT pass_id, status, reason, kernel_count, read_bytes, write_bytes, read_ratio, write_ratio, agreement_status
FROM energy_validation_kernel_traffic_passes ORDER BY pass_id DESC LIMIT 5;

SELECT kernel_name, read_bytes, write_bytes
FROM energy_validation_kernel_traffic_kernels WHERE pass_id = :p ORDER BY read_bytes + write_bytes DESC;
```

## Writing another profiling kind

Set `MEASUREMENT = "profiling"` on a `StepKindABC`. `measure()` gets `ctx["db"]` and `ctx["attempt_id"]`, writes its own extension tables in one transaction, and raises `StepFailed(code, detail)` when the work did not complete.
