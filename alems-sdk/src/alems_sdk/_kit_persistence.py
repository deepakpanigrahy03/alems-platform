"""
alems_sdk._kit_persistence — conformance kit for persistence family.
alems_sdk._kit_output — conformance kit for output family.

Persistence: alems.databases, alems.extensions.
Output: alems.outputs.
"""
from __future__ import annotations

from alems_sdk._kit_base import ConformanceKit

PERSISTENCE_GROUPS = frozenset({
    "alems.databases",
    "alems.extensions",
})

OUTPUT_GROUPS = frozenset({
    "alems.outputs",
})


class PersistenceKit(ConformanceKit):
    """
    Conformance kit for the persistence family.

    Checks:
    1. Manifest present.
    2. Namespace declared (extension plugins must declare one).
    3. No foreign key from core tables into plugin tables (static check on
       declared tables list; runtime FK check is out of scope here).
    4. No core.* imports for external plugins (INV-14).
    5. Config schema present.
    """

    EXTENSION_POINT = "alems.extensions"

    def check(self, cls: type, meta: dict, origin: str) -> None:
        """
        Run all persistence family checks.

        Args:
            cls: The plugin class.
            meta: ALEMS_PLUGIN_META dict.
            origin: 'first_party' or 'external'.
        """
        self.check_manifest(meta)
        self.check_config_schema(cls, origin)
        self.check_no_core_imports(cls, origin)

        group = meta.get("extension_point", "")
        if "extension" in group:
            self._check_namespace(cls, meta)

    def _check_namespace(self, cls: type, meta: dict) -> None:
        """Extension plugins must declare a namespace."""
        namespace = meta.get("namespace") or getattr(cls, "NAMESPACE", None)
        if not namespace:
            self.fail(
                "persistence/extension plugin missing namespace in meta or NAMESPACE class attribute"
                " (D5.2 DESIGN_CHUNK39_v4)"
            )


class OutputKit(ConformanceKit):
    """
    Conformance kit for the output family.

    Checks:
    1. Manifest present.
    2. export() method present.
    3. No core.* imports for external plugins (INV-14).
    4. Config schema present.
    """

    EXTENSION_POINT = "alems.outputs"

    def check(self, cls: type, meta: dict, origin: str) -> None:
        """
        Run all output family checks.

        Args:
            cls: The plugin class.
            meta: ALEMS_PLUGIN_META dict.
            origin: 'first_party' or 'external'.
        """
        self.check_manifest(meta)
        self.check_config_schema(cls, origin)
        self.check_no_core_imports(cls, origin)
        self._check_export(cls)

    def _check_export(self, cls: type) -> None:
        """Output plugins must expose an export() method."""
        if not hasattr(cls, "export"):
            self.fail("output plugin missing export() method")
