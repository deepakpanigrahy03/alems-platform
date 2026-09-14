"""
================================================================================
EXTENSION ABC — Abstract Base Class and Post-Run Payload Contract
================================================================================

PURPOSE:
    Defines the contract every A-LEMS research extension must implement.
    Extensions observe completed runs and write derived data to their own tables.
    They cannot modify core measurements (INV-1, INV-3).

WHY THIS EXISTS:
    Core owns measurement. Extensions own research-specific derived data.
    This boundary is enforced structurally, not by convention:
      - PostRunPayload is frozen (dataclass frozen=True).
      - on_post_run() is called only after core commits energy_uj.
      - No before-run or during-run hook exists.

UNITS:
    energy_uj  : microjoules (integer)
    duration_ns: nanoseconds (integer)

AUTHOR: Deepak Panigrahy
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PostRunPayload:
    """
    Read-only snapshot of a completed core run.

    Passed to every active extension's on_post_run() method after
    core has committed energy_uj, duration_ns, and baseline_id to
    the runs table (INV-1 enforcement: core owns those writes).

    frozen=True means no field can be assigned after construction.
    Any attempt raises AttributeError immediately — this is structural
    enforcement, not a documentation convention.

    The db field is the live DatabaseInterface instance.
    Extensions use it to query core tables (read) and write to their
    own extension tables (write). Attempting to INSERT into core
    measurement columns will fail because extensions only created
    their own tables through their own migrations.

    Fields:
        run_id       : Primary key in the runs table.
        exp_id       : Foreign key to experiments table.
        hw_id        : Foreign key to hardware_config table.
        workflow_type: "agentic" or "linear".
        model_name   : Model identifier from provider config.
        energy_uj    : Package energy in microjoules (core committed).
        duration_ns  : Run duration in nanoseconds (core committed).
        status       : "completed", "failed", or "timeout".
        baseline_id  : Idle baseline subtracted for this run.
        db           : DatabaseInterface instance for queries and writes.
    """

    run_id: int
    exp_id: int
    hw_id: int
    workflow_type: str
    model_name: str
    energy_uj: int
    duration_ns: int
    status: str
    baseline_id: str
    db: Any  # DatabaseInterface — typed as Any to avoid circular import


class ExtensionABC(ABC):
    """
    Abstract base class for all A-LEMS research extensions.

    An extension adds per-machine, activatable research capability:
    new database tables, new runtime data collection, new derived metrics.
    It integrates with the platform through a single typed post-run
    interface. There is no before-run or during-run hook.

    Every extension must implement all abstract methods.
    EXTENSION_VERSION is a required class attribute (not instance).

    Lifecycle (per machine):
        pip install → add to app_settings.yaml [extensions] active
        → alems_migrate.py runs extension migrations
        → on_activate() called once
        → on_post_run() called after every completed run
        → (optional) remove from active list → on_deactivate() called
        → tables and historical data preserved indefinitely

    Python version: 3.9+ (no match/case, no X|Y type hints).
    """

    # Subclasses must set this as a class attribute, e.g.:
    #   EXTENSION_VERSION = "1.0.0"
    EXTENSION_VERSION: str

    @abstractmethod
    def get_name(self) -> str:
        """
        Stable identity string for this extension.

        Must match the entry point name in pyproject.toml and the name
        listed in [extensions] active in app_settings.yaml.
        Never changes after first activation on a machine — it is
        recorded in extension_registry and used as the primary key.

        Returns:
            Snake-case identity string, e.g. "output_quality".
        """

    @abstractmethod
    def get_version(self) -> str:
        """
        Extension version string.

        Used for logging and extension_registry recording.
        Should follow semver.

        Returns:
            Version string, e.g. "1.0.0".
        """

    @abstractmethod
    def get_migrations_dir(self) -> Optional[Path]:
        """
        Path to this extension's SQL migration files.

        The migration runner discovers e###_*.sql files here.
        Return None if this extension needs no database tables.
        Typical implementation:
            return Path(__file__).parent / "migrations"

        Returns:
            Path to migrations directory, or None.
        """

    @abstractmethod
    def get_tables(self) -> List[str]:
        """
        List of table names this extension owns.

        Used by the deactivation system to identify which tables
        belong to this extension. Must match exactly what the
        extension's migrations create.

        Returns:
            List of table name strings, e.g. ["output_quality", "run_quality"].
        """

    @abstractmethod
    def on_activate(self, db: Any) -> None:
        """
        Called once when this extension is first activated on a machine.

        Migrations have already run by the time this is called, so all
        tables declared in get_tables() exist. Use this for seed data
        or one-time initialization beyond schema creation.

        Must not raise. Wrap all logic in try/except and log failures.

        Args:
            db: DatabaseInterface instance for setup queries/writes.
        """

    @abstractmethod
    def on_deactivate(self, db: Any) -> None:
        """
        Called when this extension is removed from the active list.

        Tables and all historical data are preserved.
        Only runtime callbacks are unregistered.
        Use for any cleanup meaningful at deactivation time.

        Must not raise. Wrap all logic in try/except and log failures.

        Args:
            db: DatabaseInterface instance for cleanup queries/writes.
        """

    @abstractmethod
    def on_post_run(self, payload: PostRunPayload) -> None:
        """
        Called after every core run is committed to the database.

        Core has already written energy_uj, duration_ns, and baseline_id.
        Those values are in payload and cannot be modified.
        This method reads payload fields and writes derived data to the
        extension's own tables.

        Must not raise. Wrap all logic in try/except and log failures.
        An unhandled exception here would propagate to experiment_runner
        and risk corrupting the experiment session state.

        Args:
            payload: Frozen snapshot of the completed run. See PostRunPayload.
        """

    @classmethod
    def get_config_schema(cls) -> Dict:
        """
        Declare configuration keys this extension reads from app_settings.yaml.

        Keys are read from the plugins.<extension_name> section.
        The framework validates these keys at activation time.
        Return an empty dict if no configuration is needed.

        Schema format:
            {
                "key_name": {
                    "type": "str",       # one of: str, int, float, bool
                    "required": False,
                    "default": None,
                }
            }

        Returns:
            Dict mapping config key names to their schema dicts.
        """
        return {}
