# Thermal Exposure Extension

The `thermal_exposure` extension holds the measurement setup for thermal exposure studies: which power channels and temperature sensors a machine has and what they mean, a room temperature reader interface, a sensor value-change probe, and the tables used by continuous session recording.

## What it owns { #thermal-exposure-tables }

All tables are created by the extension migration `migrations/extensions/thermal_exposure/e001_create_thermal_exposure.sql`, only on machines where `thermal_exposure` is listed in `extensions.active` in `config/app_settings.yaml`. They are not part of the core schema, so `alems dev status` (which shows the core schema version) does not change when they are added; the extension migration is listed by `alems dev migrate --status`.

| Table | One row per | Filled by |
|-------|-------------|-----------|
| `te_channel` | power channel per machine profile | profile loader at activation |
| `te_sensor` | temperature sensor per machine profile (stable metadata only) | profile loader at activation |
| `te_session` | thermal session | session recorder |
| `te_idle_window` | idle window per channel or sensor | session checks |
| `te_clock_anchor` | clock anchor every 10 s | session recorder |
| `te_session_check` | quality check per session | session checks |
| `te_ambient_samples` | room temperature reading | session recorder (NULL plus status `UNAVAILABLE` while no probe is fitted) |

Room temperature has its own table because the core thermal tables cannot hold an external probe: zones absent from sysfs are deactivated at every harness start, the zone reader reads sysfs files only, and `thermal_samples_v2.temp_celsius` is NOT NULL.

## Machine profiles { #thermal-exposure-profiles }

A machine profile (`config/machine_profiles/<name>.yaml`, a logical name, never a hostname) holds decisions only: which channels and sensors the analysis uses, each channel's role (`component`, `aggregate` or `overlapping`), its boundary, and the main channel and main sensor. Facts come from the hardware catalog discovered on the machine (`power_rails`, `energy_domains`, `thermal_zones`). The loader rejects a profile, with every problem listed and nothing written, if it has duplicate names, an unknown source table or key, an invalid unit, a main channel or sensor that is not listed, or a source with no usable reader on the machine.

A channel with an unknown boundary, or an overlapping channel, is stored with `attribution_eligible = 0` and never enters the energy-attribution model. An aggregate and its own components are never used together.

Select the profile in `config/app_settings.yaml`:

```yaml
thermal_exposure:
  machine_profile: gpu_soc_a
```

The extension loads the profile on its first activation on a machine. After editing a profile, reload it:

```bash
python3 scripts/tools/thermal_profile_reload.py --check   # validate only
python3 scripts/tools/thermal_profile_reload.py           # validate and write
```

## Value-change probe { #thermal-exposure-probe }

```bash
python3 scripts/tools/thermal_value_change_probe.py --seconds 60 --out <scratch file>
```

Reads every thermal zone and the GPU temperature as fast as possible and reports, per sensor, the number of reads, the number of value changes, the observed intervals between changes (median, p10, p90) and the resolution. The result is evidence of update behaviour, not a hardware rate. The probe writes to no store and never sets a polling rate.

## Room temperature reader { #thermal-exposure-ambient }

`extensions/thermal_exposure/ambient_reader.py` defines the reader interface for one external probe, registered in the `alems.readers.thermal` entry point group with `FIDELITY = "LIMITED"`. Its `can_handle()` returns False, so it is never selected as the machine's thermal reader. Until a probe is fitted, every reading returns no value with status `UNAVAILABLE`. To add a probe, subclass `AmbientReader`, implement `_read_probe_celsius()` and `is_available()`, and raise the AMBIENT bound in `alems_sdk.measurement_schema` with evidence.

## Platform coverage { #thermal-exposure-platforms }

| Platform | Profile | Status |
|----------|---------|--------|
| NVIDIA Grace GB10 (GN100, aarch64) | `gpu_soc_a` (10 rails, 7 ACPI zones, GPU temperature) | VERIFIED |
| Intel i7-1165G7 (x86_64) | none yet; extension activates with empty registers | PENDING |
| AMD Ryzen (x86_64) | none yet | PENDING |
| Apple M1 Pro (arm64) | none yet | PLANNED |

## Continuous experiment recording { #thermal-exposure-recording }

`extensions/thermal_exposure/recorder.py` (`ExperimentRecorder`) records an experiment between its runs: a pre-window before the first run, every gap, and an optional cool-down. It is registered in the `alems.recorders` entry point group; `core/recording/session.py` builds the platform readers (the same discovery and `ReaderFactory` calls as the harness), a writer factory on the resolved store, and loads the recorder. `core/recording/hook.py` is called by the harness just before a run's measurement starts (`notify_run_start`, the recorder pauses and stops reading) and just after it stops (`notify_run_end`, the recorder resumes); with no recording open both calls do nothing.

Samples inside a run stay in the core sample tables under the run's `run_id`. Samples outside runs go to `te_thermal_samples`, `te_cooling_samples`, `te_gpu_temp_samples`, `te_power_rail_samples` and `te_ambient_samples`, keyed to one `te_session` row (no row in `runs`). Sample times follow the time line in [Experiment Recording](../research/experiment-recording.md). Writes go through a short-lived `InProcessWriter` per flush (every 2 s), so the recorder never holds the store lock while a run saves; a failed write keeps the rows for the next flush. A recording left open by a dead process is marked `aborted` at the next start (`recover_open_sessions`, idempotent, samples untouched).

Opt in with `--record-continuous` (`--record-pre-window-s`, `--record-cooldown-max-s`) on `run_experiment.py`, or a `recording:` section in an experiment config (see `config/experiment_configs/examples/example_record_continuous.yaml`). `scripts/tools/thermal_record.py --seconds N` records N seconds with no load and prints the checks.
