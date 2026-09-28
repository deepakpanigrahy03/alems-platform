---
**Status:** PRODUCTION
**Schema version:** 116 (schema_namespace_tables)
**Platforms verified:** NVIDIA Grace GB10 (aarch64)
**Last updated:** 2026-09-28
---

# Schema Namespaces

## Overview

A schema namespace is a label that declares which feature owns a set of database
tables and views. Before chunk 39.5, every table was implicitly owned by core.
This meant that DDL changes for EAR tables, recovery tables, or output quality
tables went into `migrations/schema/` alongside measurement foundation changes.

Namespaces fix this. After 39.5, each non-core table has a recorded owner in
`schema_namespace_tables`. Future DDL for that table goes only into
`migrations/extensions/<namespace>/`. Core migrations stay clean and frozen
after Gate F.

## The 11 Namespaces

| Namespace | Tables owned | Purpose |
|---|---|---|
| attribution | energy_attribution, v_attribution_summary, v_fraction_verification | Energy attribution derived state |
| failure_recovery | failure_injection_log, goal_attempt, recovery_events, recovery_taxonomy, tool_failure_events, views | EAR, retry, recovery, injection |
| gui | analysis_domain_config, analysis_view_config, metric_analysis_domains, metric_display_registry, page_* | GUI layout and display config |
| llm_tracking | machine_setup_history | LLM call tracking |
| network_energy | network_energy_attribution | Network energy attribution |
| orchestration | ear_policy, ear_decision_log, ear_policy_rules, failure_taxonomy, orchestration_events, retry_policy, serving_runtime_snapshots, tool_selection_events | Orchestration and serving |
| outlier_detection | outlier_detection_config, run_outliers | Statistical outlier detection |
| output_quality | hallucination_events, output_quality, output_quality_judges, run_quality, v_quality_energy_frontier | LLM output quality scoring |
| power_limits | power_limits, power_rail_samples, run_power_limits | Power cap and rail measurement |
| task_categories | task_categories | Task classification |
| telemetry | cache_state_snapshots, device_telemetry, state_reuse_events, state_reuse_taxonomy, v_state_reuse_impact | Cache and state reuse telemetry |

## How Ownership Is Recorded

`schema_namespace_tables` is a core table (owned by core, created in v116):

```sql
SELECT namespace, object_name, object_kind
FROM schema_namespace_tables
ORDER BY namespace, object_name;
```

`extension_registry` records each namespace as active:

```sql
SELECT name, status, version FROM extension_registry ORDER BY name;
```

`migration_history` records each namespace adoption marker:

```sql
SELECT source, version FROM migration_history
WHERE source != 'core' ORDER BY source, version;
```

## Adding DDL to a Namespace

Never add DDL for a namespace table to `migrations/schema/`. Instead:

1. Add a new file `migrations/extensions/<namespace>/eNNN_description.sql`.
2. Number `NNN` sequentially within the namespace (e000 is the adoption marker).
3. The file contains DDL only (ALTER TABLE, CREATE INDEX). No INSERT or UPDATE.
4. Run `alems dev migrate --run`. The migration applies and records in
   `migration_history` with `source='ext:<namespace>'`.
5. Update `core/database/schema.py` so a fresh install produces the same result
   (MSC-5 two step rule).

## Adding a New Namespace

A new namespace is needed when a new plugin owns tables not covered by the
11 existing namespaces.

1. Add an entry to `extension_registry`:
   ```sql
   INSERT INTO extension_registry (name, status, version)
   VALUES ('<namespace>', 'active', '1.0.0');
   ```
   Do this via a seed migration `migrations/seed/sNNN_register_<namespace>.sql`.

2. Create `migrations/extensions/<namespace>/e000_adopt.sql` with the adoption
   marker sentinel (`SELECT 1;`).

3. Seed `schema_namespace_tables` rows for the new tables via
   `migrations/seed/sNNN_<namespace>_tables.sql`.

4. Add table CREATE statements to `core/database/schema.py` and wire them in
   `core/database/sqlite_adapter.py` (SC-6, MSC-5).

## Invariants

INV-15: Core tables never hold foreign keys into namespace tables.
Namespace tables may reference core tables (runs, goal_execution, etc).

INV-2: Registration only through entry points and the registry service.
No namespace is hardcoded anywhere outside `extension_registry`.

## Verification

```bash
# Count tables per namespace
sqlite3 "$DB" "SELECT namespace, COUNT(*) FROM schema_namespace_tables GROUP BY namespace ORDER BY namespace;"

# All 11 namespaces active
sqlite3 "$DB" "SELECT name, status FROM extension_registry ORDER BY name;"

# Fresh install equals migrated install
alems sandbox create /tmp/test-ns-check
sqlite3 /mnt/alems-data/$(hostname)/sandboxes/test-ns-check/experiments.db \
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name;" \
    > /tmp/fresh.txt
sqlite3 "$DB" \
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name;" \
    > /tmp/live.txt
diff /tmp/fresh.txt /tmp/live.txt && echo "PASS: fresh equals migrated"
```
