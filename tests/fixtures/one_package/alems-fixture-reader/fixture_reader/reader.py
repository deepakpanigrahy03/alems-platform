"""
fixture_reader.reader — constant-power synthetic energy reader for one-package test.

Depends only on alems_sdk. Never imports core.*
"""
from __future__ import annotations

from typing import Dict, Optional

# Import only from alems_sdk per INV-14.
from alems_sdk.types import Fidelity

# Plugin identity declared at class level so conformance kit can find it.
_META = {
    "plugin_id": "fixture_constant",
    "family": "measurement",
    "extension_point": "alems.readers.energy",
    "version": "0.1.0",
    "sdk_range": ">=0.9.0",
}


class ConstantEnergyReader:
    """
    Constant-power energy reader for testing.

    Returns a fixed 1 W reading regardless of platform.
    FIDELITY = MEASURED so conformance kit exercises the standard path.
    """

    ALEMS_PLUGIN_META: dict = _META

    # Fidelity vocabulary from compliance section 2 of SPEC_39_00_COMMON.
    FIDELITY: str = "MEASURED"
    ERROR_BOUND = None

    # Constant power in microwatts for a 1-second window.
    _CONSTANT_UW: int = 1_000_000  # 1 W

    @classmethod
    def is_available(cls) -> bool:
        """Always available on any platform — this is a fixture."""
        return True

    @classmethod
    def get_name(cls) -> str:
        return "fixture_constant"

    @classmethod
    def get_config_schema(cls) -> dict:
        """Minimal JSON Schema for plugin config."""
        return {
            "type": "object",
            "properties": {
                "constant_uw": {
                    "type": "integer",
                    "default": 1_000_000,
                    "description": "Constant power output in microwatts.",
                }
            },
            "additionalProperties": False,
        }

    def start_monitoring(self) -> None:
        """No-op start for fixture."""
        pass

    def stop_monitoring(self) -> Dict[str, object]:
        """Return a fixed energy reading summary."""
        return {
            "package_energy_uj": self._CONSTANT_UW,
            "fidelity": self.FIDELITY,
            "reader": self.get_name(),
        }

    def read_energy_uj(self) -> Dict[str, int]:
        """Return constant package energy in microwatts."""
        return {"package": self._CONSTANT_UW}
