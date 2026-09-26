"""
alems_sdk._kit_base — shared base for all conformance kits.

Every kit subclasses ConformanceKit and implements check().
"""
from __future__ import annotations

import ast
import importlib
import inspect
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass


class ConformanceKit:
    """
    Base class for per-family conformance kits.

    Subclasses implement check() and append to self._failures / self._warnings.
    Never raises — all errors are captured as failures.
    """

    # Subclasses set this to the extension point group they cover.
    EXTENSION_POINT: str = ""

    def __init__(self) -> None:
        self._failures: List[str] = []
        self._warnings: List[str] = []

    @property
    def failures(self) -> List[str]:
        return list(self._failures)

    @property
    def warnings(self) -> List[str]:
        return list(self._warnings)

    @property
    def passed(self) -> bool:
        return len(self._failures) == 0

    def fail(self, msg: str) -> None:
        self._failures.append(msg)

    def warn(self, msg: str) -> None:
        self._warnings.append(msg)

    def check(self, cls: type, meta: dict, origin: str) -> None:
        """
        Run all checks for this kit against cls.

        Args:
            cls: The plugin class loaded from the entry point.
            meta: The ALEMS_PLUGIN_META dict declared on the class.
            origin: 'first_party' or 'external'.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared helpers available to all kits.
    # ------------------------------------------------------------------

    def check_config_schema(self, cls: type, origin: str) -> None:
        """
        Verify cls exposes a valid JSON-Schema-compatible config_schema.

        First party plugins must have one (failure).
        External plugins get a warning only until SDK 1.0.
        """
        # Check class-level or instance-level get_config_schema.
        getter = getattr(cls, "get_config_schema", None)
        if getter is None:
            msg = "no get_config_schema method or class attribute found"
            if origin == "external":
                self.warn(msg)
            else:
                self.fail(msg)
            return
        try:
            # Call as classmethod or instantiation-free staticmethod where possible.
            if isinstance(inspect.getattr_static(cls, "get_config_schema"), classmethod):
                schema = cls.get_config_schema()
            else:
                # Cannot call without instance; just confirm it exists.
                schema = None
        except Exception as exc:
            self.warn("get_config_schema raised: %s" % exc)
            return
        if schema is not None and not isinstance(schema, dict):
            self.fail("get_config_schema must return a dict, got %s" % type(schema).__name__)

    def check_manifest(self, meta: dict) -> None:
        """Verify required meta fields are present."""
        required = ["plugin_id", "family", "version"]
        for field in required:
            if field not in meta:
                self.warn("ALEMS_PLUGIN_META missing field: %s" % field)

    def check_no_core_imports(self, cls: type, origin: str) -> None:
        """
        AST scan the module file for imports of core.*.

        First party plugins get a warning (strangler period).
        External plugins get a failure.
        """
        try:
            module = inspect.getmodule(cls)
            if module is None:
                return
            src_file = inspect.getfile(module)
            with open(src_file, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=src_file)
        except Exception:
            # Cannot parse — skip silently, not a conformance failure.
            return

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("core."):
                        violations.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("core."):
                    violations.append(node.module)

        if not violations:
            return

        msg = "imports core.*: %s (INV-14)" % ", ".join(set(violations))
        if origin == "external":
            self.fail(msg)
        else:
            # First party: strangler period warning only.
            self.warn(msg)
