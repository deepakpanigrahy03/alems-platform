"""
core/serving/discovery.py: compatibility path for capability discovery.

Since 39.5.1a the mixin lives in alems_sdk.serving.discovery. This module
re-exports the identical objects (D2.2, Rule S). New code imports from the SDK.
"""

from alems_sdk.serving.discovery import (  # noqa: F401  (re-export)
    CapabilityDiscoveryMixin,
    _KV_PREFIXES,
    _QUEUE_PREFIXES,
)

__all__ = ["CapabilityDiscoveryMixin"]
