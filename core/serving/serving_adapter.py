"""
core/serving/serving_adapter.py: compatibility path for the serving contract.

Since 39.5.1a the contract lives in alems_sdk.serving (D2.1). This module
re-exports the identical objects so every existing core import, isinstance
check and registry key keeps working unchanged (D2.2, Rule S).
New code imports from alems_sdk.serving.
"""

from alems_sdk.serving import (  # noqa: F401  (re-export)
    CacheState,
    EngineInfo,
    ExpertTierState,
    QueueState,
    RequestMetrics,
    ServingCapabilities,
    ServingEndpoint,
    ServingEngineAdapter,
    TokenRateState,
)

__all__ = [
    "ServingCapabilities",
    "RequestMetrics",
    "CacheState",
    "ExpertTierState",
    "QueueState",
    "TokenRateState",
    "EngineInfo",
    "ServingEngineAdapter",
    "ServingEndpoint",
]
