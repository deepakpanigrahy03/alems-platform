# A-LEMS Extension System Developer Guide

---
**Platform version:** A-LEMS 1.0 (schema version 89+)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64), AMD Ryzen (x86_64)
**Status:** PRODUCTION
**Last updated:** 2026-09-14
---

## Table of Contents

1. [Why Extensions Exist](#1-why-extensions-exist)
2. [Architecture Overview](#2-architecture-overview)
3. [The Measurement Boundary](#3-the-measurement-boundary)
4. [What an Extension Can and Cannot Do](#4-what-an-extension-can-and-cannot-do)
5. [Building Your First Extension](#5-building-your-first-extension)
6. [Extension Migrations](#6-extension-migrations)
7. [Activation and Lifecycle](#7-activation-and-lifecycle)
8. [The Post-Run Payload](#8-the-post-run-payload)
9. [Testing with the Synthetic Platform](#9-testing-with-the-synthetic-platform)
10. [Publishing as a pip Package](#10-publishing-as-a-pip-package)
11. [Reference: Full API Contract](#11-reference-full-api-contract)
12. [Reference: Troubleshooting](#12-reference-troubleshooting)

---

## 1. Why Extensions Exist

A-LEMS measures the physical energy cost of AI inference at the hardware counter level.
That measurement pipeline is fixed, auditable, and reproducible.
It does not change between experiments, platforms, or research questions.

Research, however, does change.
One paper asks: how does orchestration overhead scale with tool call count?
Another asks: what does it cost to verify that an answer was correct?
A third asks: which failure class costs the most energy to recover from?

Each of these requires new database tables, new runtime data collection, and new analysis queries.
None of them should touch the measurement pipeline.

The extension system separates these two concerns cleanly.
Core measures.
Extensions observe the results and record derived research data.

This separation has a practical consequence that matters for reproducibility.
You can activate an extension on one machine, deactivate it on another, and the core
measurement on both machines produces identical `energy_uj` and `duration_ns` values.
The presence or absence of an extension is provably irrelevant to what the hardware read.


---

## 2. Architecture Overview

The diagram below shows how data flows from hardware to extension tables.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          A-LEMS CORE                                    │
│                                                                         │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────────────┐    │
│  │   Hardware  │    │  EnergyEngine │    │   experiment_runner.py  │    │
│  │   Counters  │───▶│  (measures)  │───▶│   save_pair()           │    │
│  │ RAPL / SPBM │    │              │    │   save_single()         │    │
│  └─────────────┘    └──────────────┘    └────────────┬────────────┘    │
│                                                       │                 │
│                                         Core commits: │                 │
│                                         energy_uj     │                 │
│                                         duration_ns   │                 │
│                                         baseline_id   │                 │
│                                                       │                 │
│                                         Builds read-only payload        │
│                                                       │                 │
└───────────────────────────────────────────────────────┼─────────────────┘
                                                        │
                                         PostRunPayload │  (frozen, read-only)
                                                        │
                              ┌─────────────────────────▼──────────────────┐
                              │           ExtensionManager                  │
                              │                                             │
                              │   for each active extension:                │
                              │       extension.on_post_run(payload)        │
                              └──────┬──────────────┬──────────────┬───────┘
                                     │              │              │
                              ┌──────▼──────┐ ┌────▼──────┐ ┌────▼──────┐
                              │ ext-output  │ │ext-network│ │ext-custom │
                              │  -quality   │ │  -energy  │ │  (yours)  │
                              │             │ │           │ │           │
                              │ writes to:  │ │ writes to:│ │ writes to:│
                              │ output_     │ │ network_  │ │ your own  │
                              │ quality     │ │ energy_   │ │ tables    │
                              │ run_quality │ │ attrib.   │ │           │
                              └─────────────┘ └───────────┘ └───────────┘
```

Two things are worth noting in this diagram.

First, the payload travels in one direction only.
Core produces it after committing.
Extensions receive it.
There is no return path.
An extension cannot change what core already wrote.

Second, each extension writes only to its own tables.
The database handle in the payload carries write access only to tables
that the extension's own migrations created.
An extension that tries to insert into `runs` will get an error,
not a silent corruption.


### The Registry

Every extension is registered in the `extension_registry` table at activation time.

```
extension_registry
┌──────────────────────┬──────────┬──────────────────────┬────────┬──────────────────┐
│ name                 │ version  │ activated_at         │ status │ migration_version │
├──────────────────────┼──────────┼──────────────────────┼────────┼──────────────────┤
│ orchestration        │ 1.0.0    │ 2026-01-15 09:00:00  │ active │ e003             │
│ output_quality       │ 1.0.0    │ 2026-09-14 11:00:00  │ active │ e001             │
│ failure_recovery     │ 1.0.0    │ 2026-01-15 09:00:00  │ legacy │ NULL             │
└──────────────────────┴──────────┴──────────────────────┴────────┴──────────────────┘
```

`status = active` means the extension runtime is loaded and `on_post_run` fires after every run.
`status = legacy` means the extension tables exist (from before the extension system shipped)
but the runtime was tagged retroactively, not explicitly activated.
`status = inactive` means the extension was previously active, then deactivated.
Its tables and historical data remain in the database.


---

## 3. The Measurement Boundary

Understanding what the measurement boundary is matters before writing any extension code.

```
                    ┌─── MEASUREMENT BOUNDARY ───────────────────────────┐
                    │                                                     │
  start_measurement()                                              stop_measurement()
        │           │                                                │    │
        ▼           │   ┌────────────────────────────────────────┐  │    │
  RAPL/SPBM         │   │         LLM inference runs here        │  │    │
  counters read ────┼──▶│   adapter.call(prompt, temperature)    │──┼───▶│ RAPL/SPBM
                    │   │                                        │  │    │ counters read
                    │   └────────────────────────────────────────┘  │    │
                    │                                                │    │
                    └────────────────────────────────────────────────────┘
                                                                     │
                                                              energy_delta_uj
                                                                     │
                                                                     ▼
                                                             committed to runs
                                                                     │
                                                                     ▼
                                                          PostRunPayload built HERE
                                                                     │
                                                                     ▼
                                                        on_post_run(payload) called
```

The extension receives control only after `energy_delta_uj` is committed.
It is causally downstream.
This is not a convention.
It is enforced by the call order in `save_pair()` and `save_single()`.

The research implication is significant.
When an extension runs an LLM-as-judge scorer to evaluate answer quality,
that scorer's own energy consumption does not contaminate the inference measurement.
The inference energy was committed before the scorer ran.
The scorer's energy, if measured, goes into the extension's own tables.
This is the Observer Energy separation described in the A-LEMS research contribution.


---

## 4. What an Extension Can and Cannot Do

### What an Extension Can Do

```
✅  Read any core table via payload.db
✅  Write to tables your own migrations created
✅  Read the PostRunPayload fields (run_id, energy_uj, duration_ns, etc.)
✅  Make additional database queries to get more context
✅  Call external services (APIs, scoring models) for derived metrics
✅  Create as many of your own tables as your research needs
✅  Register configuration schema via get_config_schema()
✅  Run cleanup logic in on_deactivate()
✅  Run setup logic in on_activate()
✅  Be packaged as a pip-installable plugin
```

### What an Extension Cannot Do

```
✅  Modify payload.energy_uj            → AttributeError (frozen dataclass)
✅  Modify payload.duration_ns          → AttributeError (frozen dataclass)
✅  INSERT into runs table              → FK violation / permission error
✅  INSERT into another extension's tables → not your tables, not your migrations
✅  Run during measurement              → only on_post_run exists, nothing before
✅  Intercept or modify an LLM response → no before/during hook exists
✅  Access core code internals          → only the payload API is supported
```

The absence of before-run and during-run hooks is deliberate.
A hook that fires before measurement could skew the measurement.
A hook that fires during inference could affect the inference itself.
The post-run interface is the only safe integration point.


---

## 5. Building Your First Extension

This section walks through building `ext-carbon-tracker`, a simple extension
that records a carbon estimate alongside every run.
It touches every part of the system you need to understand.

### 5.1 Directory Structure

```
extensions/
└── carbon_tracker/
    ├── __init__.py
    ├── extension.py          ← your ExtensionABC implementation
    └── migrations/
        └── e001_create_carbon_metrics.sql
```

All extensions live under `extensions/` in the platform repository.
External plugins packaged for pip have a slightly different layout,
covered in Section 10.

### 5.2 The Extension Class

```python
# extensions/carbon_tracker/extension.py

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.extensions.abc import ExtensionABC, PostRunPayload

logger = logging.getLogger(__name__)

# Grams of CO2 per kilowatt-hour for ERCOT grid (Texas).
# This is a configuration value in production; hardcoded here for clarity.
_ERCOT_CARBON_INTENSITY_G_PER_KWH = 386.0


class CarbonTrackerExtension(ExtensionABC):
    """
    Records a carbon estimate for every A-LEMS run.

    Uses energy_uj from the core run record and a configurable grid carbon
    intensity to compute grams of CO2 per run.
    Records the result in the carbon_metrics table.

    This extension does not affect core energy measurement in any way.
    It reads energy_uj from the payload after core has committed it.
    """

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        """Return the stable identity string for this extension."""
        return "carbon_tracker"

    def get_version(self) -> str:
        """Return the extension version string."""
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        """Return the path to this extension's migration files."""
        # __file__ is extensions/carbon_tracker/extension.py
        # so migrations/ is a sibling of this file
        return Path(__file__).parent / "migrations"

    def get_tables(self) -> List[str]:
        """Return the table names this extension owns."""
        return ["carbon_metrics"]

    def get_config_schema(self) -> Dict:
        """
        Declare configuration keys this extension reads from app_settings.yaml.

        The framework validates these keys at activation time.
        Researchers set them under [plugins.carbon_tracker] in app_settings.yaml.
        """
        return {
            "carbon_intensity_g_per_kwh": {
                "type": float,
                "default": _ERCOT_CARBON_INTENSITY_G_PER_KWH,
                "description": "Grid carbon intensity in grams CO2 per kWh.",
            }
        }

    def on_activate(self, db) -> None:
        """
        Called once when this extension is first activated on a machine.

        Migrations have already run by the time this is called,
        so carbon_metrics exists. Use this for any seed data or
        one-time setup beyond schema creation.
        """
        logger.info("carbon_tracker extension activated")

    def on_deactivate(self, db) -> None:
        """
        Called when this extension is removed from the active list.

        Tables and historical data are not deleted.
        Only runtime callbacks are unregistered.
        """
        logger.info("carbon_tracker extension deactivated — historical data preserved")

    def on_post_run(self, payload: PostRunPayload) -> None:
        """
        Called after every core run is committed.

        Reads energy_uj from the payload and computes a carbon estimate.
        Writes the result to carbon_metrics.

        Args:
            payload: Read-only snapshot of the completed run.
                     See PostRunPayload in core/extensions/abc.py for all fields.
        """
        # Guard: skip runs that did not complete successfully.
        # A failed run has unreliable energy_uj.
        if payload.status != "completed":
            logger.debug(
                "carbon_tracker: skipping run_id=%d with status=%s",
                payload.run_id,
                payload.status,
            )
            return

        # Convert microjoules to kilowatt-hours.
        # 1 uJ = 1e-6 J; 1 kWh = 3.6e9 J
        energy_kwh = payload.energy_uj / 3.6e15

        # Apply grid carbon intensity.
        carbon_g = energy_kwh * _ERCOT_CARBON_INTENSITY_G_PER_KWH

        # Write to our own table.
        # payload.db provides the database handle.
        # We can only write to carbon_metrics (our table).
        try:
            payload.db.conn.execute(
                """
                INSERT INTO carbon_metrics
                    (run_id, energy_uj, carbon_g, grid_intensity_g_per_kwh, computed_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    payload.run_id,
                    payload.energy_uj,
                    carbon_g,
                    _ERCOT_CARBON_INTENSITY_G_PER_KWH,
                ),
            )
            payload.db.conn.commit()
            logger.debug(
                "carbon_tracker: run_id=%d carbon=%.4f g",
                payload.run_id,
                carbon_g,
            )
        except Exception as exc:
            # Never crash the experiment runner.
            # Log the error and return.
            # The core run record is already committed and safe.
            logger.error(
                "carbon_tracker: failed to write carbon_metrics for run_id=%d: %s",
                payload.run_id,
                exc,
            )
```

Several design decisions in this code are worth naming explicitly.

The `status != "completed"` guard prevents writing carbon estimates for failed runs.
This matters because a failed run may have partial energy readings.
Writing a carbon estimate from a partial reading would produce a misleading number.

The `try/except` around the database write is mandatory.
If the extension raises an unhandled exception, the framework logs it and continues.
The core run record is already committed and is never at risk.
But the extension's own data would be lost for that run.
The explicit `try/except` with logging gives the researcher visibility into failures.

The comment `# We can only write to carbon_metrics (our table)` is documentation,
not enforcement.
The structural enforcement comes from the migration system: the extension only created
`carbon_metrics`, so attempting to write to any other table risks a missing column
error or a foreign key violation.


### 5.3 The `__init__.py`

```python
# extensions/carbon_tracker/__init__.py

# Extension identity metadata.
# This is read by the plugin discovery system.
ALEMS_PLUGIN_META = {
    "name": "carbon_tracker",
    "version": "1.0.0",
    "alems_compat": ">=1.0,<2.0",
    "description": "Records CO2 carbon estimate per run using grid carbon intensity.",
    "platform_constraint": None,  # works on all platforms
}
```


---

## 6. Extension Migrations

Extension migrations are SQL files that create the tables your extension owns.
They run only on machines where your extension is active.
They never run on machines where your extension is not listed.

This is the key difference from core migrations, which run on every machine always.

### 6.1 Migration File Naming

```
migrations/extensions/<extension_name>/e001_<description>.sql
migrations/extensions/<extension_name>/e002_<description>.sql
```

The `e` prefix distinguishes extension migrations from core migrations (`v` prefix)
and seed migrations (`s` prefix).
The number is sequential within the extension.
Extension migrations from different extensions share no numbering space.

### 6.2 Writing a Migration

```sql
-- migrations/extensions/carbon_tracker/e001_create_carbon_metrics.sql
-- Extension: carbon_tracker
-- Creates the carbon_metrics table.
-- This migration runs only on machines where carbon_tracker is active.

CREATE TABLE IF NOT EXISTS carbon_metrics (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES runs(run_id),
    energy_uj               INTEGER NOT NULL,
    carbon_g                REAL NOT NULL,
    grid_intensity_g_per_kwh REAL NOT NULL,
    computed_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_carbon_metrics_run_id
    ON carbon_metrics(run_id);

INSERT INTO schema_version (version, applied_at, description)
VALUES (
    (SELECT COALESCE(MAX(version), 0) + 1 FROM schema_version),
    datetime('now'),
    'ext-carbon-tracker: create carbon_metrics table'
);
```

Three rules govern extension migration files.

First, always use `CREATE TABLE IF NOT EXISTS`.
Extension migrations may run on a machine that already has the table
from a previous activation.
`IF NOT EXISTS` makes the migration safe to re-run.

Second, always reference `runs(run_id)` as the foreign key to core.
Extension tables may reference core tables.
They must never reference other extensions' tables.
If your research needs data from two extensions, join in your query,
not in your schema.

Third, always insert into `schema_version`.
This allows the migration runner to track which schema version corresponds
to each extension table creation.
The `(SELECT COALESCE(MAX(version), 0) + 1 FROM schema_version)` expression
produces the next sequential version number automatically.

### 6.3 Migration Isolation in Practice

```
Machine A (GN100)                    Machine B (AMD Ryzen)
──────────────────────               ──────────────────────
app_settings.yaml:                   app_settings.yaml:
  [extensions]                         (no [extensions] section)
  active = carbon_tracker

alems_migrate.py runs:               alems_migrate.py runs:
  1. Core migrations (v089+)           1. Core migrations (v089+)
  2. Reads [extensions] active         2. No [extensions] → legacy mode
  3. Runs e001_create_carbon.sql       3. Extension migrations: SKIPPED
  4. carbon_metrics table: EXISTS      4. carbon_metrics table: ABSENT

alems run:                           alems run:
  CarbonTrackerExtension fires         No extension callbacks
  carbon_metrics gets a row            extension_registry: no carbon row
```

The machines diverge at the point of extension activation.
Core schema is identical on both.
Core measurements are identical on both.


---

## 7. Activation and Lifecycle

### 7.1 Activating an Extension

Add the extension name to `app_settings.yaml`:

```yaml
# config/app_settings.yaml

# ... existing config ...

extensions:
  active:
    - carbon_tracker
    - output_quality
```

Then run the migration runner:

```bash
python3 scripts/tools/alems_migrate.py
```

The runner sees `[extensions] active = carbon_tracker, output_quality`,
finds the pending migrations for each, runs them, and records the activation
in `extension_registry`.

On the next `alems run`, the ExtensionManager discovers both extensions,
loads their classes, and registers their `on_post_run` callbacks.

### 7.2 Deactivating an Extension

Remove the extension name from `app_settings.yaml`:

```yaml
extensions:
  active:
    - output_quality
    # carbon_tracker removed
```

No migration is needed to deactivate.
The ExtensionManager simply does not load the extension runtime.
`carbon_metrics` keeps all its historical data.
You can still query it directly:

```bash
sqlite3 $ALEMS_DB "SELECT * FROM carbon_metrics ORDER BY id DESC LIMIT 10;"
```

### 7.3 The Full Lifecycle

```
                pip install alems-ext-carbon-tracker
                              │
                              ▼
                Add to app_settings.yaml [extensions] active
                              │
                              ▼
                python3 scripts/tools/alems_migrate.py
                    ├─ runs e001_create_carbon_metrics.sql
                    ├─ creates carbon_metrics table
                    └─ inserts row into extension_registry
                              │
                              ▼
                on_activate(db) called once
                    └─ any one-time setup logic
                              │
                              ▼
              ┌──── alems run ────────────────────────────┐
              │                                           │
              │  Core measurement runs                    │
              │  Core commits energy_uj                   │
              │  ExtensionManager builds PostRunPayload   │
              │  on_post_run(payload) called              │
              │  carbon_metrics row inserted              │
              │                                           │
              └───────────────────────────────────────────┘
                         (repeats for every run)
                              │
                              ▼
              Remove from [extensions] active
                              │
                              ▼
              on_deactivate(db) called once
                    └─ any cleanup logic (optional)
                              │
                              ▼
              Extension runtime unloaded
              carbon_metrics table: PRESERVED
              Historical data: INTACT
              Can reactivate any time
```

### 7.4 Checking Extension Status

```bash
# Which extensions are registered on this machine?
sqlite3 $ALEMS_DB "
SELECT name, version, status, activated_at
FROM extension_registry
ORDER BY activated_at;
"

# How many rows has carbon_tracker written?
sqlite3 $ALEMS_DB "
SELECT COUNT(*) as total_runs,
       ROUND(AVG(carbon_g), 4) as avg_carbon_g,
       ROUND(SUM(carbon_g), 2) as total_carbon_g
FROM carbon_metrics;
"
```


---

## 8. The Post-Run Payload

`PostRunPayload` is a frozen dataclass.
Frozen means immutable: no field can be assigned after construction.
Any attempt raises `AttributeError` immediately.

```python
# core/extensions/abc.py (reference)

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PostRunPayload:
    """
    Read-only snapshot passed to every active extension after a core run commits.

    All fields are populated by core before on_post_run() is called.
    Extensions may read any field. No field may be modified.

    The db field provides database access. Extensions use it to query core
    tables and write to their own tables. Core measurement columns in the
    runs table are not writable through this handle.
    """

    run_id: int
    exp_id: int
    hw_id: int
    workflow_type: str      # "agentic" or "linear"
    model_name: str
    energy_uj: int          # core-committed package energy in microjoules
    duration_ns: int        # core-committed run duration in nanoseconds
    status: str             # "completed", "failed", "timeout"
    baseline_id: str        # which idle baseline was subtracted
    db: Any                 # DatabaseInterface instance (read core, write own)
```

### 8.1 Using the db Handle

The `db` field is the same `DatabaseInterface` instance that core uses.
You can query any core table through it.
The constraint on writing to extension tables only is structural: your extension
only created its own tables, so those are the only tables where your INSERT
statements will succeed cleanly.

```python
def on_post_run(self, payload: PostRunPayload) -> None:
    # Read additional context from a core table.
    # This is fine — extensions may read any core table.
    rows = payload.db.conn.execute(
        "SELECT domain, energy_uj FROM energy_sample_domains WHERE run_id = ?",
        (payload.run_id,)
    ).fetchall()

    # Compute something from the domain breakdown.
    gpu_energy = sum(r[1] for r in rows if r[0] == "gpu")

    # Write to your own table only.
    payload.db.conn.execute(
        "INSERT INTO my_extension_table (run_id, gpu_energy_uj) VALUES (?, ?)",
        (payload.run_id, gpu_energy)
    )
    payload.db.conn.commit()
```

### 8.2 Payload Field Reference

| Field | Type | Typical Value | Notes |
|---|---|---|---|
| `run_id` | int | 1042 | Primary key in `runs` table |
| `exp_id` | int | 87 | Foreign key to `experiments` table |
| `hw_id` | int | 3 | Foreign key to `hardware_config` table |
| `workflow_type` | str | `"agentic"` | `"agentic"` or `"linear"` |
| `model_name` | str | `"llama3.1:8b"` | Model identifier from provider config |
| `energy_uj` | int | 54480000 | Package energy, microjoules |
| `duration_ns` | int | 18500000000 | Wall-clock duration, nanoseconds |
| `status` | str | `"completed"` | `"completed"`, `"failed"`, `"timeout"` |
| `baseline_id` | str | `"bl_20260901_001"` | References `idle_baselines` |
| `db` | DatabaseInterface | — | Use for queries and writes |


---

## 9. Testing with the Synthetic Platform

The synthetic platform lets you run the full A-LEMS pipeline, including extension
callbacks, without physical hardware.
Energy readings come from a YAML fixture file instead of RAPL or SPBM counters.
The values are deterministic and configurable.

This is how you test your extension in CI or on a development machine
that lacks the target hardware.

### 9.1 Activating the Synthetic Platform

```bash
# Override platform detection to use synthetic readers.
# This environment variable is checked before any hardware probe.
export ALEMS_PLATFORM_OVERRIDE=synthetic

# Verify the platform is recognized.
python3 -c "
from core.platform.registry import PlatformRegistry
caps = PlatformRegistry().detect()
print('platform_class:', caps.platform_class)
print('measurement_mode:', caps.measurement_mode)
"
# Expected output:
# platform_class: synthetic
# measurement_mode: MEASURED
```

### 9.2 Test Pattern: Extension Activates and Writes

```python
# tests/test_extension_system.py  (excerpt)

import os
import sqlite3
import tempfile
import pytest

from core.extensions.manager import ExtensionManager
from core.extensions.abc import PostRunPayload


def test_carbon_tracker_writes_on_post_run(tmp_path):
    """
    Verify that CarbonTrackerExtension writes to carbon_metrics
    when on_post_run is called with a valid payload.

    Uses an in-memory SQLite database to avoid any dependency on
    the production database path.
    """
    # Set up a minimal in-memory database with just the tables we need.
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE runs (
            run_id INTEGER PRIMARY KEY,
            energy_uj INTEGER,
            status TEXT
        );
        INSERT INTO runs VALUES (1, 54480000, 'completed');

        CREATE TABLE carbon_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            energy_uj INTEGER NOT NULL,
            carbon_g REAL NOT NULL,
            grid_intensity_g_per_kwh REAL NOT NULL,
            computed_at TEXT NOT NULL
        );
    """)

    # Build a minimal payload with the synthetic values.
    class FakeDB:
        pass
    fake_db = FakeDB()
    fake_db.conn = conn

    payload = PostRunPayload(
        run_id=1,
        exp_id=1,
        hw_id=1,
        workflow_type="agentic",
        model_name="test-model",
        energy_uj=54480000,
        duration_ns=18500000000,
        status="completed",
        baseline_id="test_baseline",
        db=fake_db,
    )

    # Import and instantiate the extension.
    from extensions.carbon_tracker.extension import CarbonTrackerExtension
    ext = CarbonTrackerExtension()

    # Call on_post_run directly — no experiment runner needed.
    ext.on_post_run(payload)

    # Verify the row was written.
    row = conn.execute(
        "SELECT run_id, energy_uj, carbon_g FROM carbon_metrics WHERE run_id = 1"
    ).fetchone()
    assert row is not None, "carbon_metrics row was not written"
    assert row[0] == 1
    assert row[1] == 54480000
    assert row[2] > 0, "carbon_g should be positive"
    conn.close()


def test_payload_is_frozen():
    """
    Verify that PostRunPayload cannot be modified.
    This is a structural guarantee, not just a convention.
    """
    class FakeDB:
        pass

    payload = PostRunPayload(
        run_id=1, exp_id=1, hw_id=1,
        workflow_type="agentic", model_name="test",
        energy_uj=1000, duration_ns=1000000,
        status="completed", baseline_id="test",
        db=FakeDB(),
    )

    with pytest.raises(AttributeError):
        payload.energy_uj = 999999   # must raise — payload is frozen


def test_legacy_mode_when_no_extensions_configured(tmp_path):
    """
    Verify that ExtensionManager returns legacy mode when no [extensions]
    section is present in app_settings.yaml.

    Legacy mode means all existing direct writes in experiment_runner.py
    continue to execute, preserving behavior on machines that have not
    opted into selective extension mode.
    """
    # Write an app_settings.yaml with no [extensions] section.
    settings = tmp_path / "app_settings.yaml"
    settings.write_text("database:\n  engine: sqlite\n")

    manager = ExtensionManager(config_path=str(settings))
    assert manager.is_legacy_mode() is True
```

### 9.3 End-to-End Test with Synthetic Platform

This test verifies the full pipeline: synthetic energy reader, extension activation,
migration, and post-run callback.

```bash
# Run with synthetic platform override.
export ALEMS_PLATFORM_OVERRIDE=synthetic

# Activate your extension in app_settings.yaml first, then:
python3 scripts/tools/alems_migrate.py
# Expected: "Applied extension migration: carbon_tracker e001"

# Run one experiment.
python3 -m core.execution.tests.test_harness \
    --task-id gsm8k_basic \
    --repetitions 1 \
    --provider local \
    --verbose

# Verify your extension wrote data.
python3 -c "
import sys
sys.path.insert(0, 'scripts/tools')
from path_loader import get_alems_db_path
import sqlite3
db = sqlite3.connect(get_alems_db_path())
rows = db.execute('SELECT * FROM carbon_metrics ORDER BY id DESC LIMIT 3').fetchall()
for r in rows:
    print(r)
"
```


---

## 10. Publishing as a pip Package

When your extension is ready to share, package it as a pip-installable plugin.
The package structure follows a naming convention that makes the adapter family clear.

### 10.1 Package Layout

```
alems-ext-carbon-tracker/
├── pyproject.toml
├── README.md
├── LICENSE
└── alems_ext_carbon_tracker/
    ├── __init__.py           ← ALEMS_PLUGIN_META
    ├── extension.py          ← CarbonTrackerExtension(ExtensionABC)
    └── migrations/
        └── e001_create_carbon_metrics.sql
```

### 10.2 pyproject.toml

```toml
[project]
name = "alems-ext-carbon-tracker"
version = "1.0.0"
description = "Carbon footprint estimation extension for A-LEMS"
requires-python = ">=3.9"
dependencies = ["alems-platform>=1.0,<2.0"]
license = {text = "MIT"}

[project.entry-points."alems.extensions"]
carbon_tracker = "alems_ext_carbon_tracker.extension:CarbonTrackerExtension"
```

The entry point declaration is how the A-LEMS plugin discovery system finds your extension.
When a researcher runs `pip install alems-ext-carbon-tracker`, Python registers this entry
point.
The next time `alems_migrate.py` runs, it queries `importlib.metadata.entry_points(group="alems.extensions")`
and discovers your class automatically.
No manual registration is needed.

### 10.3 Version Compatibility

Declare which A-LEMS versions your extension supports in `ALEMS_PLUGIN_META`:

```python
ALEMS_PLUGIN_META = {
    "name": "carbon_tracker",
    "version": "1.0.0",
    "alems_compat": ">=1.0,<2.0",
    "description": "Carbon footprint estimation per run.",
    "platform_constraint": None,
}
```

`alems_compat` is checked at discovery time against `alems.__version__`.
An incompatible extension is skipped with a clear log message.
It is never loaded silently.

If a researcher explicitly listed your extension in `[extensions] active` and it fails
to load, the startup raises an error rather than silently skipping.
A researcher who explicitly activated a plugin must know if it failed.

### 10.4 Testing Your Published Package

```bash
# Create a clean virtual environment.
python3 -m venv /tmp/test-ext-env
source /tmp/test-ext-env/bin/activate

# Install A-LEMS and your extension.
pip install alems-platform
pip install alems-ext-carbon-tracker

# Test with synthetic platform — no hardware needed.
export ALEMS_PLATFORM_OVERRIDE=synthetic
alems run --task gsm8k_basic --repetitions 1

# Verify discovery.
python3 -c "
from importlib.metadata import entry_points
eps = entry_points(group='alems.extensions')
for ep in eps:
    print(ep.name, '->', ep.value)
"
# Expected:
# carbon_tracker -> alems_ext_carbon_tracker.extension:CarbonTrackerExtension
```


---

## 11. Reference: Full API Contract

### ExtensionABC

All methods your extension class must implement.

```python
class ExtensionABC(ABC):

    EXTENSION_VERSION: str
    # Class attribute. Set on your subclass, not an instance attribute.

    @abstractmethod
    def get_name(self) -> str:
        """
        Stable identity string for this extension.
        Must match the entry point name in pyproject.toml.
        Must match the name listed in [extensions] active in app_settings.yaml.
        Never changes after first activation on a machine.
        Example: "carbon_tracker"
        """

    @abstractmethod
    def get_version(self) -> str:
        """
        Extension version string.
        Used for logging and extension_registry recording.
        Follows semver. Example: "1.0.0"
        """

    @abstractmethod
    def get_migrations_dir(self) -> Optional[Path]:
        """
        Path to this extension's migration SQL files.
        Return None if your extension needs no database tables.
        Typically: Path(__file__).parent / "migrations"
        """

    @abstractmethod
    def get_tables(self) -> List[str]:
        """
        List of table names this extension owns.
        Used by the deactivation system to identify which tables belong here.
        These must match exactly what your migrations create.
        Example: ["carbon_metrics", "carbon_daily_summary"]
        """

    @abstractmethod
    def on_activate(self, db) -> None:
        """
        Called once when this extension is first activated on a machine.
        Migrations have already run. Tables exist.
        Use for seed data or one-time initialization.
        Must not raise. Log errors and return.
        """

    @abstractmethod
    def on_deactivate(self, db) -> None:
        """
        Called when extension is removed from [extensions] active.
        Tables and data are not deleted.
        Only runtime callbacks are unregistered.
        Use for any cleanup that makes sense at deactivation.
        Must not raise. Log errors and return.
        """

    @abstractmethod
    def on_post_run(self, payload: PostRunPayload) -> None:
        """
        Called after every core run is committed.
        The core measurement is already in the database and cannot be modified.
        Read payload fields to understand what just ran.
        Use payload.db to query core tables and write to your own tables.
        Must not raise. Wrap all logic in try/except and log failures.
        """

    @abstractmethod
    def get_config_schema(self) -> Dict:
        """
        Declare configuration keys your extension reads from app_settings.yaml.
        Keys are read from [plugins.<your_extension_name>] section.
        Return empty dict if you need no configuration.
        Example:
            return {
                "threshold": {"type": float, "default": 0.5, "description": "..."}
            }
        """
```

### PostRunPayload Fields (complete)

| Field | Type | Description |
|---|---|---|
| `run_id` | `int` | Primary key in `runs` table for this run |
| `exp_id` | `int` | Foreign key to `experiments` table |
| `hw_id` | `int` | Foreign key to `hardware_config` table |
| `workflow_type` | `str` | `"agentic"` or `"linear"` |
| `model_name` | `str` | Model identifier from provider config |
| `energy_uj` | `int` | Committed package energy in microjoules |
| `duration_ns` | `int` | Committed run duration in nanoseconds |
| `status` | `str` | `"completed"`, `"failed"`, or `"timeout"` |
| `baseline_id` | `str` | Idle baseline subtracted for this run |
| `db` | `DatabaseInterface` | Database handle for queries and writes |

All fields are read-only.
Attempting to assign to any field raises `AttributeError`.

### Migration File Naming

```
migrations/extensions/<extension_name>/e<NNN>_<description>.sql

Rules:
  - e prefix is mandatory (distinguishes from v=schema, s=seed)
  - NNN is three-digit, zero-padded, sequential within the extension
  - description uses underscores, lowercase
  - Never reuse numbers within an extension (fix forward)

Examples:
  e001_create_carbon_metrics.sql
  e002_add_carbon_source_column.sql
  e003_create_carbon_daily_summary.sql
```

### app_settings.yaml Extensions Section

```yaml
extensions:
  active:
    - carbon_tracker
    - output_quality

plugins:
  carbon_tracker:
    carbon_intensity_g_per_kwh: 386.0
  output_quality:
    judge_model: "llama3.1:8b"
    judge_temperature: 0.0
```

The `extensions.active` list determines which extensions load at runtime.
The `plugins.<name>` section provides configuration values read via `get_config_schema()`.
Both sections are optional.
If `extensions` is absent entirely, the system runs in legacy mode.


---

## 12. Reference: Troubleshooting

### Extension Not Loading

**Symptom:** Extension listed in `[extensions] active` but no rows in its tables.

```bash
# Check if the extension was discovered.
python3 -c "
from importlib.metadata import entry_points
eps = entry_points(group='alems.extensions')
print([ep.name for ep in eps])
"

# Check extension_registry.
sqlite3 $ALEMS_DB "SELECT name, status, activated_at FROM extension_registry;"

# Check the migration runner output.
python3 scripts/tools/alems_migrate.py --status
```

The most common cause is a typo between the extension name in `[extensions] active`
and the `get_name()` return value in the extension class.
They must match exactly.

### Migration Did Not Run

**Symptom:** Extension is in `extension_registry` but its table does not exist.

```bash
# Check migration_history for extension entries.
sqlite3 $ALEMS_DB "
SELECT version, name, source, status, applied_at
FROM migration_history
WHERE source LIKE 'ext:%'
ORDER BY applied_at DESC LIMIT 10;
"

# Re-run the migration runner.
python3 scripts/tools/alems_migrate.py
```

If the table still does not exist after re-running, check that `get_migrations_dir()`
returns the correct path and that the SQL file is present at that path.

### on_post_run Not Being Called

**Symptom:** Migration ran, table exists, but no rows are inserted during experiments.

```bash
# Check that the extension is active (not legacy or inactive).
sqlite3 $ALEMS_DB "
SELECT name, status FROM extension_registry WHERE name = 'carbon_tracker';
"

# Check the experiment runner log for errors.
grep "carbon_tracker" logs/a-lems.log | tail -20
```

An extension with `status = legacy` will not automatically have its `on_post_run`
registered in selective mode.
Set it explicitly in `[extensions] active`.

### Payload Modification Error

**Symptom:** `AttributeError: cannot assign to field 'energy_uj'`

This is expected behavior, not a bug.
`PostRunPayload` is frozen.
No field may be modified after construction.
If your extension logic needs to transform a value, store the result in a local
variable before writing to your own table.

```python
# WRONG
payload.energy_uj = payload.energy_uj / 1000  # raises AttributeError

# RIGHT
energy_mj = payload.energy_uj / 1000          # local variable is fine
conn.execute("INSERT INTO my_table (energy_mj) VALUES (?)", (energy_mj,))
```

### Extension Table Already Exists Error

**Symptom:** Migration fails with `table carbon_metrics already exists`

This happens when a migration was run previously without `IF NOT EXISTS`.
Fix the migration to use `CREATE TABLE IF NOT EXISTS` going forward.
For the current machine, the table already has the right schema.
Mark the migration as applied in `migration_history` manually:

```bash
sqlite3 $ALEMS_DB "
INSERT INTO migration_history (version, name, source, status, applied_at)
VALUES (1, 'e001_create_carbon_metrics.sql', 'ext:carbon_tracker', 'applied', datetime('now'));
"
```

Then re-run `alems_migrate.py`.
It will see the migration as already applied and skip it.

---

## Summary

The extension system gives you three things.

First, a clean way to add research-specific database tables without touching core
measurement code.
Your extension creates its tables, your extension writes to them, and core measurements
are identical whether your extension is active or not.

Second, per-machine activation.
An extension active on your primary measurement machine does not need to be active
on a colleague's machine running the same core experiments.
Data collection diverges; measurement methodology does not.

Third, a path to pip-installable research modules.
An extension you build today following this guide can be published to PyPI and
installed by any A-LEMS user with a single `pip install` command.
The platform discovers it automatically.
The researcher activates it with one line in `app_settings.yaml`.
No modifications to platform source code are needed or allowed.

The first extension that validates all of this infrastructure is `ext-output-quality`,
which measures the energy cost of LLM-as-judge quality evaluation alongside the energy
cost of the inference being evaluated.
That is the Observer Energy problem the extension architecture was designed to solve.
