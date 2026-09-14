"""
================================================================================
EXTENSION MANAGER — Discovery, Lifecycle, and Post-Run Dispatch
================================================================================

PURPOSE:
    Manages the full lifecycle of A-LEMS research extensions:
      - Reads [extensions] active from app_settings.yaml
      - Discovers extension classes from built-in registry
      - Runs on_activate() for newly activated extensions
      - Dispatches on_post_run(payload) to each active extension
      - Detects legacy mode (no [extensions] section = today's behavior)

LEGACY MODE:
    When [extensions] is absent from app_settings.yaml, this manager
    returns is_legacy_mode() = True. experiment_runner.py checks this
    flag and falls back to its existing direct-write code paths.
    Legacy machines see zero behavior change.

SELECTIVE MODE:
    When [extensions] active = [...] is present, only listed extensions
    are loaded. Unlisted extensions' tables remain but receive no new data.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Type

import yaml

from core.extensions.abc import ExtensionABC, PostRunPayload
from core.extensions.registry import ExtensionRegistry

logger = logging.getLogger(__name__)

# Default config path — resolved relative to repo root at import time.
# Overridable via constructor for testing.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "app_settings.yaml"


class ExtensionManager:
    """
    Manages discovery, activation, and post-run dispatch of extensions.

    One instance lives for the lifetime of an experiment session.
    Created by experiment_runner.py at startup.

    Usage:
        manager = ExtensionManager()
        if not manager.is_legacy_mode():
            manager.load_extensions(db)   # once at startup
            ...
            manager.run_post_run(payload) # after each core commit
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the manager and read extension config.

        Args:
            config_path: Path to app_settings.yaml. Uses default repo
                         location when None. Pass an alternative path
                         for unit testing.
        """
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._active_extensions: List[ExtensionABC] = []
        self._active_names: List[str] = []
        self._legacy_mode: bool = True  # safe default until config is read
        self._loaded: bool = False

        # Read [extensions] section from app_settings.yaml.
        self._active_names = self._read_active_names()

        # Legacy mode: [extensions] key is absent from config entirely.
        # This is determined at construction time and never changes.
        self._legacy_mode = (self._active_names is None)

    def is_legacy_mode(self) -> bool:
        """
        Return True when no [extensions] section exists in app_settings.yaml.

        In legacy mode, experiment_runner.py executes its existing direct-write
        code paths unchanged. All existing extension tables receive data as today.
        The researcher experiences zero change.

        Returns:
            True if legacy mode is active, False if selective mode.
        """
        return self._legacy_mode

    def load_extensions(self, db: object) -> None:
        """
        Discover and activate all extensions listed in [extensions] active.

        Called once at experiment session startup (after DB connection
        is established). Must not be called in legacy mode.

        For each name in the active list:
          1. Look up the class in the built-in extension registry.
          2. Validate that the class implements ExtensionABC.
          3. Instantiate the extension.
          4. Call on_activate() if this is the first activation on this machine.
          5. Register the on_post_run callback.

        Explicitly activated extensions that fail to load raise a startup
        error — the researcher listed it, so they must know it failed.

        Args:
            db: DatabaseInterface instance for on_activate() calls and
                for recording activation in extension_registry.
        """
        if self._legacy_mode:
            # Caller should check is_legacy_mode() before calling this.
            logger.warning("load_extensions() called in legacy mode — no-op")
            return

        if self._loaded:
            logger.warning("load_extensions() called more than once — ignored")
            return

        ext_registry = ExtensionRegistry(db)

        for name in (self._active_names or []):
            try:
                cls = self._resolve_class(name)
                if cls is None:
                    # Explicitly activated extension missing from registry.
                    raise RuntimeError(
                        f"Extension '{name}' listed in [extensions] active "
                        f"but not found in built-in registry or entry points. "
                        f"Check the extension name spelling."
                    )

                instance = cls()
                already_active = ext_registry.is_registered(name)

                if not already_active:
                    # First activation on this machine.
                    instance.on_activate(db)
                    ext_registry.record_activation(
                        name=name,
                        version=instance.get_version(),
                        migration_version=self._last_migration_version(instance),
                    )
                    logger.info("Extension activated: %s v%s", name, instance.get_version())
                else:
                    logger.info("Extension loaded (already registered): %s", name)

                self._active_extensions.append(instance)

            except Exception as exc:
                # Explicitly activated extension must not fail silently.
                logger.error("FATAL: Failed to load extension '%s': %s", name, exc)
                raise RuntimeError(
                    f"Extension '{name}' failed to load: {exc}. "
                    f"Remove it from [extensions] active or fix the error."
                ) from exc

        self._loaded = True
        logger.info(
            "ExtensionManager ready. Active extensions: %s",
            [e.get_name() for e in self._active_extensions],
        )

    def run_post_run(self, payload: PostRunPayload) -> None:
        """
        Dispatch on_post_run(payload) to every active extension.

        Called by experiment_runner.py after core commits a run.
        Each extension receives the same read-only payload.
        A failing extension is logged and skipped — it must never
        crash the experiment session or affect other extensions.

        Args:
            payload: Frozen snapshot of the completed run.
        """
        if self._legacy_mode:
            # Should not be called in legacy mode, but guard defensively.
            return

        for ext in self._active_extensions:
            try:
                ext.on_post_run(payload)
            except Exception as exc:
                # One extension crash must not stop others from running.
                # The core run record is already committed and is safe.
                logger.error(
                    "Extension '%s' on_post_run failed for run_id=%d: %s",
                    ext.get_name(),
                    payload.run_id,
                    exc,
                )

    def get_active_names(self) -> List[str]:
        """
        Return the list of extension names currently loaded.

        Returns:
            List of extension name strings.
        """
        return [e.get_name() for e in self._active_extensions]

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _read_active_names(self) -> Optional[List[str]]:
        """
        Read the [extensions] active list from app_settings.yaml.

        Returns None when the [extensions] key is entirely absent
        (triggers legacy mode). Returns an empty list when the key
        exists but active is empty (selective mode, no extensions).

        Returns:
            List of extension name strings, or None if key absent.
        """
        if not self._config_path.exists():
            logger.debug("app_settings.yaml not found at %s — legacy mode", self._config_path)
            return None

        try:
            with open(self._config_path, "r") as fh:
                config = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.warning("Could not read app_settings.yaml: %s — legacy mode", exc)
            return None

        extensions_section = config.get("extensions")
        if extensions_section is None:
            # Key absent entirely — legacy mode.
            return None

        active = extensions_section.get("active") or []
        if isinstance(active, str):
            # Support YAML scalar "active: orchestration" as well as list form.
            active = [s.strip() for s in active.split(",") if s.strip()]

        return active

    def _resolve_class(self, name: str) -> Optional[Type[ExtensionABC]]:
        """
        Resolve an extension name to its class.

        Phase 1 (this chunk): looks up built-in extensions registered
        via _BUILTIN_EXTENSIONS dict below.
        Phase 6 (35E): will also query importlib.metadata entry_points
        for externally installed extensions.

        Args:
            name: Extension identity string, e.g. "output_quality".

        Returns:
            ExtensionABC subclass, or None if not found.
        """
        return _BUILTIN_EXTENSIONS.get(name)

    def _last_migration_version(self, instance: ExtensionABC) -> Optional[str]:
        """
        Find the highest-numbered migration file for this extension.

        Used to record the migration version in extension_registry
        at activation time. Returns None if the extension has no
        migration directory or no migration files.

        Args:
            instance: The extension instance being activated.

        Returns:
            Filename of the highest-numbered migration, or None.
        """
        mdir = instance.get_migrations_dir()
        if mdir is None or not mdir.exists():
            return None

        sql_files = sorted(mdir.glob("e*.sql"))
        if not sql_files:
            return None

        # Last in sorted order is the highest migration number.
        return sql_files[-1].name


# ---------------------------------------------------------------------------
# Built-in extension registry (Phase 1 — bootstrap pattern matching 35A/35B)
#
# Add one line here when a new built-in extension is implemented.
# External extensions (pip-installed) are discovered via entry_points in 35E.
# ---------------------------------------------------------------------------

def _build_builtin_registry() -> Dict[str, Type[ExtensionABC]]:
    """
    Build the dict of built-in extension name -> class.

    Import errors for individual extensions are caught so that a broken
    extension does not prevent other extensions from loading.
    This matches the "optional plugin failure" semantics in the design doc:
    a built-in that is NOT explicitly listed in [extensions] active may
    fail to import without crashing startup.

    Returns:
        Dict mapping extension name strings to ExtensionABC subclasses.
    """
    registry: Dict[str, Type[ExtensionABC]] = {}

    # output_quality is implemented in chunk 8.5C against the real schema.
    # It is not registered here — 8.5C wires it when ready.
    pass

    # Future built-in extensions added here, one line each:
    # try:
    #     from extensions.orchestration.extension import OrchestrationExtension
    #     registry["orchestration"] = OrchestrationExtension
    # except Exception as exc:
    #     logger.debug("Built-in extension 'orchestration' not loadable: %s", exc)

    return registry


# Module-level singleton built once at import time.
_BUILTIN_EXTENSIONS: Dict[str, Type[ExtensionABC]] = _build_builtin_registry()
