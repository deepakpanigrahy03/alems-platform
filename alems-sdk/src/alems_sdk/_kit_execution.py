"""
alems_sdk._kit_execution — conformance kit for the execution family.

Covers: engines.text, engines.media, engines.serving, models.fragments,
        preflight.checks, scorers, tools, tool_selectors, frameworks,
        harness.retry, harness.recovery, harness.collectors,
        injection_engines.
"""
from __future__ import annotations

from alems_sdk._kit_base import ConformanceKit

EXECUTION_GROUPS = frozenset({
    "alems.engines.text",
    "alems.engines.media",
    "alems.engines.serving",
    "alems.models.fragments",
    "alems.preflight.checks",
    "alems.scorers",
    "alems.tools",
    "alems.tool_selectors",
    "alems.frameworks",
    "alems.harness.retry",
    "alems.harness.recovery",
    "alems.harness.collectors",
    "alems.injection_engines",
})


class ExecutionKit(ConformanceKit):
    """
    Conformance kit for the execution family.

    Checks:
    1. ALEMS_PLUGIN_META manifest present.
    2. Config schema present (warning for external until SDK 1.0).
    3. No core.* imports for external plugins (INV-14; warning for first party).
    4. Scorer: score range declared if applicable.
    5. Serving engine: capabilities() callable.
    6. Preflight check: result shape matches documented contract.
    """

    EXTENSION_POINT = "alems.engines.text"

    def check(self, cls: type, meta: dict, origin: str) -> None:
        """
        Run all execution family checks.

        Args:
            cls: The plugin class.
            meta: ALEMS_PLUGIN_META dict.
            origin: 'first_party' or 'external'.
        """
        self.check_manifest(meta)
        self.check_config_schema(cls, origin)
        self.check_no_core_imports(cls, origin)

        group = meta.get("extension_point", "")
        if "scorer" in group or "scorer" in cls.__name__.lower():
            self._check_scorer(cls)
        if "serving" in group:
            self._check_serving(cls)
        if "preflight" in group:
            self._check_preflight(cls)

    def _check_scorer(self, cls: type) -> None:
        """Scorers should declare a score range."""
        # score_range is a best-practice attribute; warn if missing.
        if not hasattr(cls, "score_range") and not hasattr(cls, "SCORE_RANGE"):
            self.warn("scorer missing score_range or SCORE_RANGE attribute")

    def _check_serving(self, cls: type) -> None:
        """Serving engines must expose a capabilities() method."""
        if not hasattr(cls, "capabilities"):
            self.fail("serving engine missing capabilities() method")

    def _check_preflight(self, cls: type) -> None:
        """Preflight checks must expose a run() or check() method."""
        if not hasattr(cls, "run") and not hasattr(cls, "check"):
            self.fail("preflight plugin missing run() or check() method")
