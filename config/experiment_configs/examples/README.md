# config/scenarios/examples/README.md

## Scenario Reference Templates

These YAML files are **platform reference templates** — they document the
scenario injection format and serve as starting points for research studies.

**Do not reference these files directly in experiment YAML configs.**
Copy the relevant template to your experiment workspace and rename it
for your study before use.

### Available templates

| File | Use for |
|---|---|
| `single_type_rate1.yaml` | Per-type cost profiling, rate=1.0 deterministic injection |
| `single_type_step_target.yaml` | Location-varied injection sweep, one step per config |
| `cascade_two_types.yaml` | Compound cascading failure studies |
| `dry_run_template.yaml` | Scenario design validation without actual injection |

### How to use in an experiment YAML

```yaml
failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "your_study_id_v1"        # unique provenance key for your study
  scenario_file: "path/to/your_copy.yaml" # path to your copied + renamed file
  dry_run: false                           # set true for design validation
```

### Adding new failure types

New failure types can be injected immediately after adding a row to
`failure_taxonomy` — no code changes or migrations required (A1 design).

```bash
sqlite3 "$DB" "INSERT INTO failure_taxonomy (failure_type_id, domain, description, default_retryable, typical_cost_rank) VALUES ('my_type', 'execution', 'My custom failure', 1, 15);"
```

Then use `type: my_type` in any scenario YAML.
