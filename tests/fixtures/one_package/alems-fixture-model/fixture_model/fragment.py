"""
fixture_model.fragment — minimal model fragment for one-package test.

Declares a single model provider definition. Depends only on alems_sdk.
"""
from __future__ import annotations

_META = {
    "plugin_id": "fixture_model",
    "family": "execution",
    "extension_point": "alems.models.fragments",
    "version": "0.1.0",
    "sdk_range": ">=0.9.0",
}


class FixtureModelFragment:
    """
    Fixture model fragment that declares one synthetic model provider.

    Used by the one-package test to verify model provider resolution
    through the plugin system without touching any real endpoint.
    """

    ALEMS_PLUGIN_META: dict = _META

    @classmethod
    def get_config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    @classmethod
    def get_models(cls) -> dict:
        """
        Return a dict of model id -> provider config fragment.

        Returns:
            Dict with one synthetic test model entry.
        """
        return {
            "fixture-model-v1": {
                "provider": "fixture",
                "endpoint": "http://localhost:0",
                "context_window": 4096,
                "description": "Fixture model for one-package conformance test.",
            }
        }
