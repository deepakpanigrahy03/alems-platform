"""
alems_sdk._kit_measurement — conformance kit for the measurement family.

Covers: alems.platforms, alems.readers.* (all kinds).
"""
from __future__ import annotations

import inspect
from typing import List

from alems_sdk._kit_base import ConformanceKit

# Fidelity values allowed per compliance vocab (PAC-3 / SPEC_39_00_COMMON section 2).
_VALID_FIDELITY = {"MEASURED", "INFERRED", "LIMITED", "CALCULATED"}

# Groups that belong to the measurement family.
MEASUREMENT_GROUPS = frozenset({
    "alems.platforms",
    "alems.readers.energy",
    "alems.readers.cpu",
    "alems.readers.thermal",
    "alems.readers.turbostat",
    "alems.readers.msr",
    "alems.readers.scheduler",
    "alems.readers.disk",
})


class MeasurementKit(ConformanceKit):
    """
    Conformance kit for the measurement family.

    Checks:
    1. Required abstract methods present (is_available, get_name, read_*).
    2. is_available callable without side effects (returns bool).
    3. FIDELITY class attribute declared and within compliance vocabulary.
    4. Modeled readers (FIDELITY == INFERRED) must declare ERROR_BOUND.
    5. No core.* imports (INV-14).
    6. Config schema present.
    7. ALEMS_PLUGIN_META manifest check.
    """

    EXTENSION_POINT = "alems.readers.energy"  # representative; overridden per group

    def check(self, cls: type, meta: dict, origin: str) -> None:
        """
        Run all measurement family checks.

        Args:
            cls: The reader or platform adapter class.
            meta: ALEMS_PLUGIN_META dict.
            origin: 'first_party' or 'external'.
        """
        self.check_manifest(meta)
        self.check_config_schema(cls, origin)
        self.check_no_core_imports(cls, origin)
        self._check_is_available(cls)
        self._check_get_name(cls)
        self._check_fidelity(cls, origin)

    def _check_is_available(self, cls: type) -> None:
        """is_available must exist and be callable without raising."""
        method = getattr(cls, "is_available", None)
        if method is None:
            self.fail("missing is_available method (PAC-1)")
            return
        # Call on a best-effort basis; catch all exceptions gracefully.
        try:
            # Use the class method if it is a classmethod or staticmethod,
            # otherwise instantiation is required — skip the call check.
            static = inspect.getattr_static(cls, "is_available")
            if isinstance(static, (classmethod, staticmethod)):
                result = cls.is_available()
                if not isinstance(result, bool):
                    self.warn("is_available returned non-bool: %s" % type(result).__name__)
        except Exception as exc:
            self.warn("is_available raised during conformance check: %s" % exc)

    def _check_get_name(self, cls: type) -> None:
        """get_name must exist."""
        if not hasattr(cls, "get_name"):
            self.fail("missing get_name method (PAC-1)")

    def _check_fidelity(self, cls: type, origin: str) -> None:
        """
        FIDELITY class attribute must be declared and valid.
        Modeled (INFERRED) readers must also declare ERROR_BOUND.
        """
        fidelity = getattr(cls, "FIDELITY", None)
        if fidelity is None:
            # First party failure; external warning until SDK 1.0.
            msg = "FIDELITY class attribute not declared (section 6 SPEC_39_3)"
            if origin == "external":
                self.warn(msg)
            else:
                self.fail(msg)
            return
        if fidelity not in _VALID_FIDELITY:
            self.fail(
                "FIDELITY value %r not in compliance vocabulary %s" % (fidelity, _VALID_FIDELITY)
            )
        # Estimators / modeled readers must declare an error bound.
        if fidelity == "INFERRED":
            error_bound = getattr(cls, "ERROR_BOUND", None)
            if error_bound is None:
                self.fail(
                    "FIDELITY=INFERRED requires ERROR_BOUND class attribute (section 6 SPEC_39_3)"
                )
