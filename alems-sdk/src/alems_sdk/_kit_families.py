"""
alems_sdk._kit_families: family kits (documented contract rules only).

Replaces _kit_measurement, _kit_execution and _kit_persistence, which encoded
rules that were never checked against real plugins (G44). Group sets are
derived from alems_sdk.manifest.GROUP_FAMILY so they cannot drift.
"""
from __future__ import annotations

from alems_sdk._kit_base import ConformanceKit
from alems_sdk.manifest import GROUP_FAMILY

# PAC-3 vocabulary for reader outputs, amended 2026-10-01 with SYNTHETIC.
FIDELITY_VALUES = frozenset({"MEASURED", "INFERRED", "LIMITED", "SYNTHETIC"})

MEASUREMENT_GROUPS = frozenset(g for g, f in GROUP_FAMILY.items() if f == "measurement")
EXECUTION_GROUPS = frozenset(g for g, f in GROUP_FAMILY.items() if f == "execution")
PERSISTENCE_GROUPS = frozenset(g for g, f in GROUP_FAMILY.items() if f == "persistence")
OUTPUT_GROUPS = frozenset(g for g, f in GROUP_FAMILY.items() if f == "output")
SEMANTIC_GROUPS = frozenset(g for g, f in GROUP_FAMILY.items() if f == "semantic")
READER_GROUPS = frozenset(g for g in MEASUREMENT_GROUPS if g.startswith("alems.readers."))


class MeasurementKit(ConformanceKit):
    """Readers declare fidelity (PAC-3, INV-13); INFERRED declares an error bound (D9.3)."""

    def check(self, cls: object, meta: dict, origin: str) -> None:
        super().check(cls, meta, origin)
        if meta.get("extension_point") not in READER_GROUPS:
            return
        fidelity = getattr(cls, "FIDELITY", None)
        value = getattr(fidelity, "value", fidelity)
        if value not in FIDELITY_VALUES:
            self.fail("FIDELITY must be one of %s, got %r"
                      % (sorted(FIDELITY_VALUES), value))
        elif value == "INFERRED" and not getattr(cls, "ERROR_BOUND", None):
            self.fail("INFERRED reader must declare ERROR_BOUND (D9.3)")
        provenance = getattr(cls, "METHOD_PROVENANCE", None)
        if provenance is not None and provenance != value:
            # G49/G53: FIDELITY is the contract; METHOD_PROVENANCE is aligned
            # in 39.5.4 with an expected diff on the methodology registry.
            self.warn("METHOD_PROVENANCE %r disagrees with FIDELITY %r (G53)"
                      % (provenance, value))


class ExecutionKit(ConformanceKit):
    """Execution family: generic contract checks only."""


class SemanticKit(ConformanceKit):
    """Semantic family: generic contract checks only."""


class PersistenceKit(ConformanceKit):
    """Extensions declare the schema namespace they own (D5.2)."""

    def check(self, cls: object, meta: dict, origin: str) -> None:
        super().check(cls, meta, origin)
        if meta.get("extension_point") == "alems.extensions":
            if not (meta.get("extra", {}).get("namespace") or getattr(cls, "NAMESPACE", None)):
                self.fail("extension must declare NAMESPACE (D5.2)")


class OutputKit(ConformanceKit):
    """Output family: generic contract checks only."""


def kit_for_group(group: str) -> ConformanceKit:
    """The kit for group; unknown groups get the generic kit (which fails them)."""
    family = GROUP_FAMILY.get(group)
    return {
        "measurement": MeasurementKit,
        "execution": ExecutionKit,
        "semantic": SemanticKit,
        "persistence": PersistenceKit,
        "output": OutputKit,
    }.get(family, ConformanceKit)()
