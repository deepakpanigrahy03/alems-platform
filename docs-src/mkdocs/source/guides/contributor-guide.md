# Contributor Guide

A-LEMS is designed for extension. Adding a new platform, energy reader,
provider, task, or documentation section follows a defined process that
confines changes to the right files and leaves everything else untouched.
This guide covers each extension point.

---

## Before You Start

Read the documentation protocol before making any change:

```
docs-src/mkdocs/source/contributing/adding-documentation.md
```

Run the build pipeline to confirm the baseline is clean:

```bash
cd ~/mydrive/alems-platform
bash scripts/build-docs.sh --validate-only
```

All changes must leave the build at 0 warnings and the validator at
0 failures.

---

## Adding a Platform

A new `platform_class` requires changes in two places only.
See [Adding a Platform](../developer/adding-a-platform.md) for the
full 4-step process.

Summary:

1. Add a detector class to `scripts/detect_hardware.py`
2. Register it in `PlatformDetector.for_current_platform()`
3. Create `scripts/platforms/<platform_class>/provision.sh` and `verify.sh`
4. Test on hardware — verify `platform_class`, `energy_measurement`, and `capability_profile`

After the platform works:
- Add a row to `reference/platform-matrix.md`
- Add methodology entries for any new energy readers
- Run `bash scripts/build-docs.sh --deploy`

---

## Adding an Energy Reader

An energy reader is a Python class that reads a hardware counter and
returns a value in microjoules. Readers live in `core/energy/readers/`.

**Step 1:** Create the reader class:

```python
# core/energy/readers/my_reader.py
class MyEnergyReader:
    def is_available(self) -> bool:
        """Return True only if the counter is present AND readable."""
        try:
            # Actually read the counter — existence check is not enough
            val = int(open("/sys/path/to/counter").read().strip())
            return val >= 0
        except Exception:
            return False

    def read_energy_uj(self) -> int:
        """Return current cumulative counter value in microjoules."""
        return int(open("/sys/path/to/counter").read().strip())
```

**Step 2:** Add the capability check to `capability_profile.py`:

```python
def _check_my_reader() -> bool:
    try:
        val = int(open("/sys/path/to/counter").read().strip())
        return val >= 0
    except Exception:
        return False
```

Add it to `build_capability_profile()` return dict.

**Step 3:** Add the method to `seed_methodology.py`. Read
`methodology_loader.py` — do not hardcode doc/section. Add an entry
to `config/methodology_docs.yaml` first.

**Step 4:** Add a section to the appropriate research doc with a
`{ #anchor }` tag matching the `method_anchor` in `methodology_docs.yaml`.

**Step 5:** Run the validator:

```bash
python3 scripts/tools/validate_methodology_refs.py
```

**Step 6:** Re-seed:

```bash
python3 scripts/seed_methodology.py
```

---

## Adding a Provider

A provider that speaks the OpenAI-compatible `/chat/completions` API
requires only a `config/models.yaml` entry. No code changes.

Add to `config/models.yaml` under `providers:`:

```yaml
providers:
  my_provider:
    transport: remote_http           # or loopback_http, inprocess
    base_url: "https://api.myprovider.com/v1"
    api_key_env: "MY_PROVIDER_API_KEY"
    openai_compat: true
    is_local: false
    energy_side: client_only         # or full, remote_measured
    cost_class: paid
    tools_supported: true
    models:
      - model_id: "my-model-7b"
        name: "My Model 7B"
        context_window: 32768
        available: true
```

Add the API key variable to `~/.alemsrc`:

```bash
export MY_PROVIDER_API_KEY=your_key_here
```

Test connectivity:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 core/execution/tests/test_llm_setup.py --provider my_provider --verbose
```

Update `reference/provider-registry.md` with the new provider entry.

---

## Adding a Task

Tasks are defined in `config/tasks.yaml` and loaded into the
`task_categories` database table at install time.

Add to `config/tasks.yaml`:

```yaml
tasks:
  - task_id: my_new_task
    name: "My New Task"
    description: "What this task measures"
    difficulty: 2                    # 1=simple, 2=multi-step, 3=complex
    workflow_types: [linear, agentic]
    tools_required: 0
    prompt_template: |
      Your prompt here. Use {variable} for dynamic content.
    expected_output_type: text
    evaluation_criteria: accuracy
```

Reload tasks into the database:

```bash
cd ~/mydrive/alems-platform && source venv/bin/activate
python3 scripts/migrate_yaml_to_db.py
```

Verify the task appears:

```bash
python3 core/execution/tests/run_experiment.py --list-tasks | grep my_new_task
```

Test with one repetition:

```bash
python3 core/execution/tests/run_experiment.py \
  --tasks my_new_task \
  --repetitions 1 \
  --provider groq \
  --workflow-mode linear \
  --experiment-type debug \
  --experiment-goal "test new task" \
  --verbose
```

Update `reference/task-registry.md` with the new task entry.

---

## Adding Documentation

Every documentation change follows the protocol in
`contributing/adding-documentation.md`. The short version:

1. Read the existing file before changing it
2. Verify every command against actual source code
3. Run `bash scripts/build-docs.sh` — must show 0 warnings
4. Commit and deploy: `bash scripts/build-docs.sh --deploy`

**Adding a new methodology section:**

1. Add the section heading with anchor to the research doc:
   ```markdown
   ## My Method Name { #my-method-anchor }
   ```
2. Add an entry to `config/methodology_docs.yaml`
3. Add the method to `seed_methodology.py`
4. Run the validator: `python3 scripts/tools/validate_methodology_refs.py`
5. Re-seed: `python3 scripts/seed_methodology.py`

**Adding a new page:**

1. Create the file in the correct section under `docs-src/mkdocs/source/`
2. Add it to `mkdocs.yml` nav
3. Run `bash scripts/build-docs.sh` to confirm 0 warnings

---

## Adding a Schema Migration

Any database schema change requires a migration file.
See [Schema Migration](../developer/schema-migration.md) for the
full migration system documentation.

Summary:

1. Create `migrations/schema/v0NN_description.sql`
2. Update `core/database/schema.py` to match
3. Test in dev: `python3 scripts/tools/alems_migrate.py`
4. Verify: `python3 scripts/tools/alems_migrate.py --status`
5. Never modify an applied migration file

If the new table or columns need documentation, add entries to
`TABLE_DESCRIPTIONS` in `scripts/tools/generate_data_dictionary.py`.
The data dictionary regenerates automatically on the next build.

---

## Code Quality Checklist

Before any pull request or commit to main:

```
□ python3 scripts/tools/validate_methodology_refs.py — 0 failures
□ bash scripts/build-docs.sh — 0 warnings
□ python3 scripts/tools/alems_migrate.py --check — no checksum errors
□ One test experiment runs cleanly end to end
□ reference/ docs updated for any new platform, provider, or task
□ TABLE_DESCRIPTIONS updated for any new DB table
```
