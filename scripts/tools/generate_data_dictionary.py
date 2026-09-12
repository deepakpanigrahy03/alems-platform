#!/usr/bin/env python3
"""
A-LEMS Data Dictionary Generator
----------------------------------
Generates docs-src/mkdocs/source/reference/data-dictionary.md from:
  - Live database schema (pragma table_info)
  - COLUMN_PROVENANCE in core/utils/provenance.py
  - measurement_method_registry table (method names, layers, confidence)
  - TABLE_DESCRIPTIONS dict in this file (one line per table — only
    hand-maintained content in this script)

Run:
    python3 scripts/tools/generate_data_dictionary.py

Runs automatically as part of scripts/build-docs.sh (Step 2).
Any new table or column added by a migration appears automatically
on the next build. No manual doc updates needed for schema changes.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

OUTPUT = REPO_ROOT / "docs-src" / "mkdocs" / "source" / "reference" / "data-dictionary.md"

# =============================================================================
# TABLE_DESCRIPTIONS — one line per table, maintained by humans
# Add a line here when a new table is created via migration.
# =============================================================================
TABLE_DESCRIPTIONS = {
    # Core measurement
    "experiments":              "One experiment per research question — parent of runs",
    "runs":                     "One row per linear or agentic workflow execution (153 columns)",
    "energy_samples":           "Raw RAPL/SPBM/IOKit counter reads at 100Hz",
    "cpu_samples":              "CPU frequency, IPC, cache counters at 10Hz",
    "thermal_samples":          "Temperature, fan RPM, voltage at 1Hz",
    "interrupt_samples":        "Context switches and interrupt ticks at 10Hz",
    "io_samples":               "Disk read/write byte deltas at 10Hz",
    "nic_samples":              "Network interface byte and packet counters at 10Hz",
    "power_rail_samples":       "SPBM per-rail power readings (NVIDIA Grace only)",
    "gpu_samples":              "DCGM GPU utilization and memory (NVIDIA Grace only)",
    "cooling_samples":          "Cooling device state and target temperature at 1Hz",
    "llm_interactions":         "One row per LLM API call within a run",
    "orchestration_events":     "Timeline of agentic orchestration phase transitions",
    # Reference / seed
    "idle_baselines":           "Idle energy baseline measurements per platform",
    "hardware_config":          "hw_config.json snapshot captured at experiment time",
    "measurement_method_registry": "Master registry of all measurement methods with formula and provenance",
    "method_references":        "Literature citations per measurement method",
    "task_categories":          "Task definitions loaded from config/tasks.yaml",
    "metric_display_registry":  "Display configuration for all metrics in GUI and reports",
    "query_registry":           "All SQL queries — no SQL hardcoded in application code",
    "normalization_factors":    "Grid intensity and environmental conversion factors per country",
    "task_quality_config":      "Quality thresholds per task type",
    "energy_domains":           "Energy domain taxonomy (pkg, core, uncore, dram, gpu)",
    "energy_sources":           "Energy source registry (rapl, spbm, iokit, dcgm, arm_pmu)",
    "power_rails":              "SPBM power rail definitions (NVIDIA Grace only)",
    "power_limits":             "Platform thermal design power limits",
    "outlier_detection_config": "Outlier detection thresholds and domain rules",
    "retry_policy":             "Retry behavior configuration per failure type",
    "analysis_domain_config":   "Analysis domain definitions for ETL and reporting",
    "analysis_view_config":     "View configuration for the clean/measured view system",
    "etl_queue":                "Pending and completed ETL job tracking",
    "migration_history":        "Applied migration versions with checksums",
    # Additional tables
    "audit_log":                    "Run-level audit events — tracks metric updates and data quality changes",
    "component_registry":           "Registered system components with schema and data shape definitions",
    "cooling_devices":              "Cooling device inventory per platform (fans, liquid coolers)",
    "cpu_idle_states":              "CPU C-state residency data per platform",
    "device_telemetry":             "Generic device telemetry samples for non-standard hardware",
    "energy_attribution":           "Phase-attributed energy values per run (planning, tool, synthesis)",
    "energy_derived_metrics":       "Computed energy metrics derived from raw samples via ETL",
    "energy_sample_domains":        "Domain assignments for energy samples (pkg, core, uncore, dram, gpu)",
    "energy_samples_v2":            "Energy samples schema v2 with domain registry integration",
    "environment_config":           "Software environment fingerprint — git commit, package versions",
    "eval_criteria":                "Evaluation criteria definitions for task quality assessment",
    "goal_attempt":                 "Individual goal attempt records within goal execution sessions",
    "goal_execution":               "Goal execution session records — multi-step agentic task tracking",
    "gpu_config":                   "GPU configuration snapshot captured at experiment time",
    "hallucination_events":         "Detected hallucination events with classification and energy cost",
    "idle_baseline_domains":        "Per-domain idle baseline power values (pkg, core, uncore, dram, gpu)",
    "machine_setup_history":        "Machine provisioning and configuration change history",
    "measurement_methodology":      "Per-run methodology audit trail — links runs to method_registry entries",
    "metric_analysis_domains":      "Analysis domain assignments for metrics in the view system",
    "network_energy_attribution":   "Network wait energy attribution per run and phase",
    "orchestration_tax_summary":    "Pre-computed orchestration tax summary per experiment",
    "output_quality":               "LLM output quality scores per run",
    "output_quality_judges":        "Judge model configurations for LLM-as-judge quality evaluation",
    "page_configs":                 "GUI page layout configurations",
    "page_metric_configs":          "Metric display configurations per GUI page",
    "page_sections":                "GUI page section definitions",
    "page_templates":               "GUI page template definitions",
    "platform_domain_relationships":"Platform-to-energy-domain mapping for cross-platform queries",
    "power_limit_events":           "Thermal throttle and power limit events during runs",
    "run_outliers":                 "Outlier detection results per run with classification and severity",
    "run_power_limits":             "Power limit state snapshots captured during runs",
    "run_quality":                  "Composite run quality scores across multiple quality dimensions",
    "schema_version":               "Current schema version tracking",
    "sqlite_sequence":              "SQLite internal auto-increment sequence tracking",
    "standardization_registry":     "Metric standardization parameters for cross-platform normalization",
    "task_retry_override":          "Per-task retry policy overrides",
    "thermal_samples_v2":           "Thermal samples schema v2 with zone registry integration",
    "thermal_zones":                "Thermal zone inventory and configuration per platform",
    "tool_failure_events":          "Tool execution failure events with classification and recovery data",
    # Views (documented separately)
}

# Columns with special explanations (supplements the method registry lookup)
COLUMN_NOTES = {
    "runs": {
        "total_energy_uj":            "Hardware counter value in µJ. Zero on modeled platforms.",
        "attributed_energy_uj":       "total_energy_uj minus idle baseline × duration. NULL until ETL runs.",
        "energy_measurement_mode":    "direct (hardware counter) / modeled (no counter) / unavailable",
        "energy_sample_coverage_pct": "% of run duration covered by energy samples. <80% = sampling gap.",
        "duration_ns":                "Inference window only — first token request to last token received.",
        "total_run_duration_ns":      "Full experiment duration including warmup, cooldown, and overhead.",
        "instructions":               "BIGINT — modern CPUs execute billions/second, exceeds INT32 range.",
        "complexity_score":           "0.4·(llm_calls/10) + 0.3·(tool_calls/10) + 0.3·(tokens/1000), capped at 1.0",
        "orchestration_tax_uj":       "Agentic energy minus equivalent linear energy — cost of coordination.",
        "orchestration_tax_pct":      "orchestration_tax_uj / total_energy_uj × 100",
        "carbon_g":                   "Calculated from energy × regional grid intensity. Not directly measured.",
        "water_ml":                   "Calculated from energy × PUE × WUE factors. Not directly measured.",
        "methane_mg":                 "Calculated from carbon_g using IPCC AR6 GWP factors. Not measured.",
        "swap_total_mb":              "Linux swap partition/file size — virtual memory overflow from RAM to disk.",
        "hw_id":                      "Hardware fingerprint from hw_config.json. Identifies the physical machine.",
        "env_id":                     "Software environment fingerprint. Identifies git commit + package versions.",
    }
}


def get_db_path():
    from scripts.tools.path_loader import get_alems_db_path
    return get_alems_db_path()


def get_all_tables(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [row[0] for row in cur.fetchall()]


def get_all_views(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    )
    return [row[0] for row in cur.fetchall()]


def get_columns(conn, table):
    cur = conn.execute(f"PRAGMA table_info('{table}')")
    return cur.fetchall()


def get_row_count(conn, table):
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM '{table}'")
        return cur.fetchone()[0]
    except Exception:
        return None


def get_method_registry(conn):
    """Load measurement_method_registry into a dict keyed by id."""
    try:
        cur = conn.execute(
            "SELECT id, name, layer, provenance, confidence, "
            "doc, method_anchor FROM measurement_method_registry"
        )
        return {
            row[0]: {
                "name": row[1], "layer": row[2], "provenance": row[3],
                "confidence": row[4], "doc": row[5], "anchor": row[6],
            }
            for row in cur.fetchall()
        }
    except Exception:
        return {}


def get_column_provenance():
    """Load COLUMN_PROVENANCE from provenance.py."""
    try:
        from core.utils.provenance import COLUMN_PROVENANCE
        return COLUMN_PROVENANCE
    except Exception:
        return {}


def format_col_type(col_type, nullable):
    t = col_type or "TEXT"
    return t if not nullable else f"{t} (nullable)"


def write_table_section(f, conn, table, method_registry, col_provenance):
    cols = get_columns(conn, table)
    row_count = get_row_count(conn, table)
    description = TABLE_DESCRIPTIONS.get(table, "")
    notes = COLUMN_NOTES.get(table, {})

    count_str = f"{row_count:,}" if row_count is not None else "—"
    desc_str = f" — {description}" if description else ""

    f.write(f"\n### `{table}`\n\n")
    f.write(f"**Rows:** {count_str}{desc_str}\n\n")

    if not cols:
        f.write("_No columns found._\n")
        return

    f.write("| # | Column | Type | Provenance | Note |\n")
    f.write("|---|---|---|---|---|\n")

    for col in cols:
        cid, name, col_type, notnull, default, pk = col
        nullable = not notnull and not pk

        # Look up provenance — COLUMN_PROVENANCE values are (method_id, provenance_type) tuples
        prov_entry = col_provenance.get(name)
        method_id = ""
        prov_type = ""
        if prov_entry and isinstance(prov_entry, tuple):
            method_id = prov_entry[0] if len(prov_entry) > 0 else ""
            prov_type = prov_entry[1] if len(prov_entry) > 1 else ""

        method_label = ""
        doc_link = ""
        if method_id and method_id in method_registry:
            m = method_registry[method_id]
            layer = m.get("layer", "")
            prov = m.get("provenance", prov_type)
            method_name = m.get("name", method_id)
            doc = m.get("doc", "")
            anchor = m.get("anchor", "")
            method_label = f"{prov} ({layer})" if layer else prov
            if doc and anchor:
                doc_link = f"[{method_name}](../research/{doc}#{anchor})"
            elif method_name:
                doc_link = method_name
        elif prov_type:
            method_label = prov_type

        note = notes.get(name, doc_link)

        type_str = format_col_type(col_type, nullable)
        f.write(f"| {cid} | `{name}` | {type_str} | {method_label} | {note} |\n")


def main():
    db_path = get_db_path()
    print(f"Reading schema from: {db_path}")

    conn = sqlite3.connect(db_path)
    method_registry = get_method_registry(conn)
    col_provenance = get_column_provenance()

    tables = get_all_tables(conn)
    views = get_all_views(conn)

    # Count total columns
    total_cols = sum(len(get_columns(conn, t)) for t in tables)

    print(f"Found {len(tables)} tables, {len(views)} views, {total_cols} columns")
    print(f"Method registry: {len(method_registry)} methods")
    print(f"Column provenance: {len(col_provenance)} entries")
    print(f"Generating: {OUTPUT}")

    with open(OUTPUT, "w") as f:
        f.write("# Data Dictionary\n\n")
        f.write(
            f"Auto-generated from the live database schema on {datetime.now().strftime('%Y-%m-%d')}. "
            f"Run `bash scripts/build-docs.sh` to regenerate after any schema migration.\n\n"
        )
        f.write(
            f"**{len(tables)} tables · {len(views)} views · {total_cols} columns**\n\n"
        )
        f.write(
            "Column provenance is sourced from `core/utils/provenance.py` "
            "(`COLUMN_PROVENANCE`) and `measurement_method_registry`. "
            "Columns without a provenance entry are marked as unregistered.\n\n"
        )

        # Table of contents
        f.write("---\n\n## Tables\n\n")
        f.write("| Table | Rows | Description |\n")
        f.write("|---|---|---|\n")
        for table in tables:
            row_count = get_row_count(conn, table)
            count_str = f"{row_count:,}" if row_count is not None else "—"
            desc = TABLE_DESCRIPTIONS.get(table, "")
            f.write(f"| [`{table}`](#{table}) | {count_str} | {desc} |\n")

        # Views list
        if views:
            f.write("\n---\n\n## Views\n\n")
            f.write(
                "A-LEMS maintains a set of filtered views for analysis. "
                "Views prefixed `v_runs_clean_` exclude confirmed outliers. "
                "Views prefixed `v_runs_measured_` exclude data quality failures "
                "but retain statistical anomalies for distribution analysis.\n\n"
            )
            f.write("| View | Purpose |\n")
            f.write("|---|---|\n")
            for view in views:
                if "clean" in view:
                    purpose = "Excludes confirmed outliers of any class"
                elif "measured" in view:
                    purpose = "Excludes data quality failures, retains statistical anomalies"
                elif "unfiltered" in view:
                    purpose = "All runs including confirmed outliers"
                else:
                    purpose = ""
                f.write(f"| `{view}` | {purpose} |\n")

        # Column detail per table
        f.write("\n---\n\n## Column Reference\n\n")
        for table in tables:
            write_table_section(f, conn, table, method_registry, col_provenance)

        f.write("\n---\n\n")
        f.write(
            f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} "
            f"from schema version recorded in `migration_history`._\n"
        )

    conn.close()
    print(f"Done. Written to {OUTPUT}")


if __name__ == "__main__":
    main()
