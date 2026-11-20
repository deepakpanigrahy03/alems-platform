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

## Thermal sensor catalog

Sensor facts are discovered on each machine, never typed into machine profiles.

**Discovery.** `scripts/detect_thermal.py` runs at install (step 4d, inside the venv, no sudo) and on demand. It asks the platform's thermal readers which sensors they read (`describe_sensors()`), adds sensors no reader records (Linux hwmon inputs, extra Mac helper sensors) with `read_by: discovery_utility`, probes everything for about 60 s and writes `config/hw_config.json` key `thermal_sensors`:

| Field | Content |
|---|---|
| `catalog` | one descriptor per temperature: id, kind (`sensor` or `derived`), interface, reported format, interface resolution, limits (trip points, NVML thresholds; unsupported ones with `temp_c` null), device, driver, label, identity key and confidence (strong, inferred, ambiguous), `read_by` |
| `catalog_sha256` | hash of the catalog facts only (no measurements, times or load), so it changes only when the machine's sensors change |
| `measurements` | per id: observed step and status, value-change interval p10/p50/p90 (only with 5 or more changes), min/median/max, advisory `possible_duplicate_of` |
| `sources`, `probe`, `provenance` | read latency and failures per reader; achieved duration, rounds, interruptions; time, kernel, GPU driver and NVML version, load and GPU use before and after |

`detect_hardware.py` never replaces this key. Options: `--dry-run` (print, write nothing), `--seconds`, `--hz`.

**Reader contract (SDK 1.1).** Both thermal reader contracts (`alems_sdk.readers.ThermalReaderABC`, `ThermalReaderV2ABC`) have optional methods:

| Method | Returns |
|---|---|
| `describe_sensors()` | descriptors (`alems_sdk.thermal_catalog.descriptor`, validated by `validate_descriptor`) |
| `read_sensors()` | `{descriptor id: C or None}` for what the reader records |
| `read_discovery_sensors()` | the same plus sensors described as `discovery_utility` |
| `describe_signals()`, `read_signals()` | non-temperature signals, typed `{value, unit, status}` (Mac thermal pressure) |

A reader that implements none of them still works: each `read_all_thermal()` key is reported as `derived` with formula `unknown (legacy reader key)`, never as a sensor. Averages and maxima are always `derived` with their formula and inputs.

**Profiles.** `thermal_exposure.machine_profile: auto` picks the profile from `platform_class` through `config/machine_profiles/index.yaml`; a platform without an entry gets no profile. A profile holds decisions only. The loader copies each sensor's resolution and its origin from the catalog into `te_sensor`, rejects a typed resolution that disagrees, and keeps a typed value without a catalog only as `typed_unverified`.

**Adding a platform.** Implement `describe_sensors()` / `read_sensors()` on its thermal reader, run `scripts/detect_thermal.py --dry-run` on the machine, then add a profile and an index entry once its sensors can be recorded per sensor.

**Mac.** Install builds the die-temperature helper from `core/readers/darwin/vendor/sensors.m` with `clang` (no sudo; the binary is not committed). Thermal pressure uses `sudo -n powermetrics` through the NOPASSWD rule install sets, and starts only in `initialize()`, never when a reader is constructed or described.
