# Conformance kit runner skeleton (SPEC_39_1 section 3).
# Individual kits are populated in phase 39.3.
# This module is importable now so plugin authors can reference it.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class KitResult:
    """Result of running one conformance kit against one plugin."""
    # Extension point group name (e.g. "readers.energy").
    extension_point: str
    plugin_id: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ConformanceReport:
    """Aggregate result of running all applicable kits."""
    plugin_id: str
    results: List[KitResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only if every kit passed."""
        return all(r.passed for r in self.results)


def run_conformance(plugin_id: str, meta: dict) -> ConformanceReport:
    """
    Discover and run all conformance kits applicable to plugin_id.

    In phase 39.1 this is a no-op skeleton: it returns a passing report
    with zero kits, because kits are populated in 39.3.

    Args:
        plugin_id: The plugin's declared name from ALEMS_PLUGIN_META.
        meta: The full ALEMS_PLUGIN_META dict.

    Returns:
        ConformanceReport with passed=True and empty results list.
    """
    # TODO(39.3): discover kits by family from entry point group alems.conformance.kits
    # and run each against the plugin under test.
    return ConformanceReport(plugin_id=plugin_id, results=[])
