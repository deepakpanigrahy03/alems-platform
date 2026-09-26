"""
alems_sdk.conformance — conformance kit runner for A-LEMS plugins.

Plugin authors call run_conformance() to validate their plugin before
shipping. The runtime calls it during discovery to populate the
conformance status shown by alems plugins list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from alems_sdk._kit_base import ConformanceKit
from alems_sdk._kit_measurement import MeasurementKit, MEASUREMENT_GROUPS
from alems_sdk._kit_execution import ExecutionKit, EXECUTION_GROUPS
from alems_sdk._kit_persistence import PersistenceKit, OutputKit, PERSISTENCE_GROUPS, OUTPUT_GROUPS


@dataclass
class KitResult:
    """Result of running one conformance kit against one plugin."""
    # Extension point group name (e.g. "alems.readers.energy").
    extension_point: str
    plugin_id: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ConformanceReport:
    """Aggregate result of running all applicable kits for one plugin."""
    plugin_id: str
    results: List[KitResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only if every kit passed."""
        return all(r.passed for r in self.results)


def _kit_for_group(group: str) -> ConformanceKit:
    """
    Return the correct kit instance for the given entry point group.

    Args:
        group: Entry point group string, e.g. 'alems.readers.energy'.

    Returns:
        A ConformanceKit subclass instance.
    """
    if group in MEASUREMENT_GROUPS:
        return MeasurementKit()
    if group in EXECUTION_GROUPS:
        return ExecutionKit()
    if group in PERSISTENCE_GROUPS:
        return PersistenceKit()
    if group in OUTPUT_GROUPS:
        return OutputKit()
    # Unknown group — return base kit that runs only manifest and import checks.
    return ConformanceKit()


def run_conformance(
    plugin_id: str,
    meta: dict,
    cls: type = None,
    origin: str = "external",
) -> ConformanceReport:
    """
    Run all conformance kits applicable to the plugin.

    Called by the runtime during discovery and by plugin authors during
    development. Never raises — all errors are captured as failures.

    Args:
        plugin_id: The plugin's declared name from ALEMS_PLUGIN_META.
        meta: The full ALEMS_PLUGIN_META dict.
        cls: The plugin class. If None, only manifest checks run.
        origin: 'first_party' or 'external'. External plugins fail on
                INV-14 core import violations; first party get warnings
                during the strangler period.

    Returns:
        ConformanceReport with per-kit results.
    """
    report = ConformanceReport(plugin_id=plugin_id)

    # Determine which group this plugin belongs to.
    group = meta.get("extension_point", "")
    if not group:
        # No group declared: run base manifest check only.
        kit = ConformanceKit()
        kit.check_manifest(meta)
        report.results.append(KitResult(
            extension_point="unknown",
            plugin_id=plugin_id,
            passed=kit.passed,
            failures=kit.failures,
            warnings=kit.warnings,
        ))
        return report

    kit = _kit_for_group(group)

    if cls is not None:
        try:
            kit.check(cls, meta, origin)
        except Exception as exc:
            # Kit itself must never propagate — capture as a failure.
            kit.fail("kit raised unexpectedly: %s" % exc)
    else:
        # No class available — manifest check only.
        kit.check_manifest(meta)

    report.results.append(KitResult(
        extension_point=group,
        plugin_id=plugin_id,
        passed=kit.passed,
        failures=kit.failures,
        warnings=kit.warnings,
    ))
    return report
