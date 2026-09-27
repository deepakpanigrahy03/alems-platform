"""
alems_sdk.attribution -- Attribution model plugin contract.

Plugin authors implement AttributionModelABC and register under
alems.attribution.models.  The runtime discovers and calls run_attribution()
after every experiment run.  The contract is deterministic: same inputs
produce the same attribution rows within declared numeric tolerance (INV-20).

Depends on nothing in core.
"""
from __future__ import annotations

import abc
from typing import Any, Dict, Optional


class AttributionPolicy:
    """
    Carries the per-run policy that the runtime passes to the model.

    Fields are advisory; a model may ignore fields it does not understand
    and must record which policy it applied on every attribution row.
    """

    def __init__(
        self,
        idle_policy: str = "legacy_baseline_subtraction",
        isolation_level: str = "exclusive",
        model_version: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        # idle_policy: named convention for idle energy allocation.
        # Initial values: legacy_baseline_subtraction, idle_time_share,
        # equal_share, reservation_share.
        self.idle_policy = idle_policy

        # isolation_level: exclusive | partitioned | shared.
        # Declared by profile or detected by observer; stored on every row.
        self.isolation_level = isolation_level

        # model_version: overrides the plugin's declared version when set.
        # Used for recomputation with a pinned version.
        self.model_version = model_version

        # extra: model-specific parameters; validated by the model.
        self.extra = extra or {}

    def as_dict(self):
        # type: () -> Dict[str, Any]
        """Return a JSON-serialisable dict for provenance recording."""
        return {
            "idle_policy": self.idle_policy,
            "isolation_level": self.isolation_level,
            "model_version": self.model_version,
            "extra": self.extra,
        }


class AttributionModelABC(abc.ABC):
    """
    Contract every attribution model plugin must implement.

    Plugin manifest must declare:
        extension_point: alems.attribution.models
        version: semver string
        sdk_range: e.g. ">=1.0.0,<2.0.0"

    The runtime calls run_attribution() after sample writes complete.
    The model writes attribution rows and residual rows via the storage
    handle.  It must not open its own database connection (INV-21, WCC-2).
    """

    @abc.abstractmethod
    def model_id(self):
        # type: () -> str
        """Stable unique identifier, e.g. 'legacy_v1'.  Never changes once published."""

    @abc.abstractmethod
    def model_version(self):
        # type: () -> str
        """Semver string, e.g. '1.0.0'."""

    @abc.abstractmethod
    def run_attribution(self, storage, run_id, policy):
        # type: (Any, int, AttributionPolicy) -> None
        """
        Compute and persist attribution for one run.

        Args:
            storage: DatabaseManager instance (the sanctioned write handle).
            run_id:  Integer run identifier already present in runs table.
            policy:  AttributionPolicy carrying idle and isolation settings.

        Returns:
            None.  All output goes to energy_attribution and
            attribution_residual via the storage handle.

        Raises:
            AttributionError on unrecoverable computation failures.
            Must never silently swallow errors (DC-3).
        """

    def supports_recompute(self):
        # type: () -> bool
        """
        Return True if the model can recompute a previously attributed run
        without side effects.  legacy_v1 returns False (UPDATE semantics).
        """
        return False


class AttributionError(Exception):
    """Raised by a model when attribution cannot complete."""
