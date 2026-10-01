"""
alems_sdk._kit_base: generic conformance checks, derived from the contracts.

Every kit runs the generic checks (manifest, INV-14 by distribution, group
contract, configuration schema). Family kits add only rules that are part of
a documented contract (reader fidelity, estimator error bound, extension
namespace). Never raises: every problem is a failure or a warning (DC-3).
"""
from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Mapping
from typing import List, Optional

from alems_sdk.config_schema import schema_shape_error
from alems_sdk.contracts import contract_for

# Manifest fields every built manifest carries (alems_sdk.manifest).
REQUIRED_MANIFEST_FIELDS = ("plugin_id", "extension_point", "family",
                            "version", "sdk_range", "description")


class ConformanceKit:
    """Generic kit; family kits subclass it and extend check()."""

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
        return not self._failures

    def fail(self, msg: str) -> None:
        self._failures.append(msg)

    def warn(self, msg: str) -> None:
        self._warnings.append(msg)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def check(self, cls: object, meta: dict, origin: str) -> None:
        """
        Run the generic checks.

        Args:
            cls: loaded plugin object (class, or callable for callable groups).
            meta: the built manifest dict.
            origin: 'runtime', 'external' or 'local' (by distribution).
        """
        self.check_manifest(meta)
        source_module = (meta.get("extra") or {}).get("source_module")
        self.check_no_core_imports(cls, origin, source_module)
        kind = self.check_contract(cls, meta.get("extension_point", ""))
        if kind == "class":
            self.check_config_schema(cls)

    # ------------------------------------------------------------------
    # Generic checks
    # ------------------------------------------------------------------

    def check_manifest(self, meta: dict) -> None:
        """The built manifest carries every required field."""
        for name in REQUIRED_MANIFEST_FIELDS:
            if not meta.get(name):
                self.fail("manifest missing field: %s" % name)

    def check_contract(self, obj: object, group: str) -> str:
        """
        The plugin implements its group's contract.

        Returns the contract kind ('class', 'callable', 'none', 'unknown').
        """
        try:
            kind, contract = contract_for(group)
        except Exception as exc:
            self.fail("contract of %s cannot be resolved: %s: %s"
                      % (group, type(exc).__name__, exc))
            return "unknown"
        if kind == "unknown":
            self.fail("no contract registered for group '%s'" % group)
        elif kind == "mapping":
            if not isinstance(obj, Mapping) or not all(isinstance(v, Mapping) for v in obj.values()):
                self.fail("group %s requires a mapping of name to definition" % group)
        elif kind == "callable":
            if not callable(obj) or isinstance(obj, type):
                self.fail("group %s requires a function" % group)
        elif kind == "class":
            if not isinstance(obj, type) or not issubclass(obj, contract):
                self.fail("does not implement %s.%s"
                          % (contract.__module__, contract.__name__))
            else:
                missing = sorted(getattr(obj, "__abstractmethods__", ()) or ())
                if missing:
                    self.fail("abstract methods not implemented: %s" % ", ".join(missing))
        else:
            self.warn("group %s has no SDK contract yet (G46)" % group)
        return kind

    def check_config_schema(self, cls: type) -> None:
        """get_config_schema() is a classmethod returning a JSON Schema object."""
        getter = inspect.getattr_static(cls, "get_config_schema", None)
        if getter is None:
            self.fail("no get_config_schema (design 7.14)")
            return
        if not isinstance(getter, classmethod):
            self.fail("get_config_schema must be a classmethod")
            return
        try:
            schema = cls.get_config_schema()
        except Exception as exc:
            self.fail("get_config_schema raised %s: %s" % (type(exc).__name__, exc))
            return
        problem = schema_shape_error(schema)
        if problem:
            self.fail(problem)

    def check_no_core_imports(self, cls: object, origin: str,
                              source_module: Optional[str] = None) -> None:
        """
        INV-14: a plugin outside the runtime distribution imports no core or scripts.

        Origin is decided by distribution; runtime components are the runtime
        and may import core (D2.1, D2.3). Unreadable source is a failure.
        """
        if origin in ("runtime", "first_party"):
            return
        try:
            # Data objects (mappings) have no module of their own: use the
            # module the entry point names.
            module = inspect.getmodule(cls)
            if module is None and source_module:
                module = importlib.import_module(source_module)
            src_file = inspect.getsourcefile(module) if module else None
            if not src_file:
                self.fail("cannot locate plugin source to verify INV-14")
                return
            with open(src_file, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=src_file)
        except Exception as exc:
            self.fail("cannot parse plugin source to verify INV-14: %s: %s"
                      % (type(exc).__name__, exc))
            return
        forbidden = ("core", "scripts")
        violations = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden:
                        violations.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and node.module.split(".")[0] in forbidden:
                    violations.add(node.module)
        if violations:
            self.fail("imports %s (INV-14)" % ", ".join(sorted(violations)))
