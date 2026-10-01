"""
fixture_estimator.estimator — fixture estimator for one-package test.

FIDELITY=INFERRED with a declared ERROR_BOUND so conformance kit
exercises the estimator path. Depends only on alems_sdk.
"""
from __future__ import annotations

from typing import Dict

_META = {
    "plugin_id": "fixture_estimator",
    "family": "measurement",
    "extension_point": "alems.readers.energy",
    "version": "0.1.0",
    "sdk_range": ">=0.9.0",
}


from alems_sdk.readers import BaseReader  # noqa: E402


class FixtureEstimator(BaseReader):
    """
    Fixture estimator. Returns a constant estimate with a declared error bound.

    FIDELITY=INFERRED per PAC-3 / compliance section 2 (SPEC_39_00_COMMON).
    ERROR_BOUND is a percentage string per methodology convention.
    """

    ALEMS_PLUGIN_META: dict = _META

    FIDELITY: str = "INFERRED"
    ERROR_BOUND: str = "±30%"  # fixture; real estimators derive from calibration

    _ESTIMATE_UW: int = 500_000  # 0.5 W estimate

    @classmethod
    def is_available(cls) -> bool:
        """Always available; real estimators check for model artefacts."""
        return True

    @classmethod
    def get_name(cls) -> str:
        return "fixture_estimator"

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def start_monitoring(self) -> None:
        pass

    def stop_monitoring(self) -> Dict[str, object]:
        return {
            "package_energy_uj": self._ESTIMATE_UW,
            "fidelity": self.FIDELITY,
            "error_bound": self.ERROR_BOUND,
            "reader": self.get_name(),
        }

    def read_energy_uj(self) -> Dict[str, int]:
        return {"package": self._ESTIMATE_UW}
