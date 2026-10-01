"""
alems_sdk.contracts: which SDK contract each plugin group implements.

Conformance is derived from this table, never from hand kept method lists
(G44): a plugin conforms when it implements its group's contract with no
abstract method left. Groups whose contract is a callable (a function taking
the documented arguments) say so explicitly. Groups with no SDK contract yet
are listed with None (G46); they receive the generic checks only.
Zero imports from core or scripts (INV-14).
"""

from __future__ import annotations

import importlib
from typing import Dict, Optional, Tuple

CALLABLE = "callable"
MAPPING = "mapping"

# group -> (SDK module, class name) | CALLABLE | None
# Reader groups use BaseReader: readers of one group implement different
# specialised ABCs today; tightening per group is a 39.5.4 reader decision.
GROUP_CONTRACT: Dict[str, object] = {
    "alems.platforms":           ("alems_sdk.platforms", "PlatformAdapterABC"),
    "alems.readers.energy":      ("alems_sdk.readers", "BaseReader"),
    "alems.readers.cpu":         ("alems_sdk.readers", "BaseReader"),
    "alems.readers.thermal":     ("alems_sdk.readers", "BaseReader"),
    "alems.readers.turbostat":   ("alems_sdk.readers", "BaseReader"),
    "alems.readers.msr":         ("alems_sdk.readers", "BaseReader"),
    "alems.readers.scheduler":   ("alems_sdk.readers", "BaseReader"),
    "alems.readers.disk":        ("alems_sdk.readers", "BaseReader"),
    "alems.engines.text":        ("alems_sdk.generation", "TextGenABC"),
    "alems.engines.media":       ("alems_sdk.generation", "MediaABC"),
    "alems.engines.serving":     ("alems_sdk.serving", "ServingEngineAdapter"),
    "alems.harness.retry":       ("alems_sdk.policies", "RetryPolicyAdapter"),
    "alems.harness.recovery":    ("alems_sdk.policies", "RecoveryPolicyAdapter"),
    "alems.harness.collectors":  ("alems_sdk.cache_telemetry", "CacheTelemetryCollector"),
    "alems.injection_engines":   ("alems_sdk.injection", "InjectionEngine"),
    "alems.scorers":             ("alems_sdk.scoring", "ScorerABC"),
    "alems.tools":               ("alems_sdk.tools", "ToolProviderABC"),
    "alems.tool_selectors":      ("alems_sdk.tool_selection", "ToolSelectorABC"),
    "alems.frameworks":          ("alems_sdk.frameworks", "FrameworkAdapterABC"),
    "alems.outputs":             ("alems_sdk.outputs", "OutputAdapterABC"),
    "alems.extensions":          ("alems_sdk.extensions", "ExtensionABC"),
    "alems.attribution.models":  ("alems_sdk.attribution", "AttributionModelABC"),
    "alems.preflight.checks":    CALLABLE,
    "alems.models.fragments":    MAPPING,    # data: provider name -> definition
    "alems.spans.vocabularies":  None,   # G46: no SDK contract yet
    "alems.databases":           None,   # G46: storage backend contract not wired
}


def contract_for(group: str) -> Tuple[str, Optional[type]]:
    """
    Resolve the contract of group.

    Returns:
        ("class", cls) | ("callable", None) | ("mapping", None) | ("none", None) | ("unknown", None)
    """
    if group not in GROUP_CONTRACT:
        return "unknown", None
    spec = GROUP_CONTRACT[group]
    if spec is None:
        return "none", None
    if spec == CALLABLE:
        return "callable", None
    if spec == MAPPING:
        return "mapping", None
    module, name = spec
    return "class", getattr(importlib.import_module(module), name)


__all__ = ["CALLABLE", "MAPPING", "GROUP_CONTRACT", "contract_for"]
