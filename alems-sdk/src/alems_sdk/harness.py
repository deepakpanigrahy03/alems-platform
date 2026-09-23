# Re-exports from core harness ABCs.
# Sources: core/retry/retry_adapter.py, core/recovery/recovery_adapter.py,
#          core/injection/injection_engine.py, core/telemetry/cache_collector.py,
#          core/execution/{scorers,tools,frameworks,outputs}/abc.py,
#          core/execution/tools/selector_abc.py

from core.retry.retry_adapter import RetryPolicyAdapter
from core.recovery.recovery_adapter import (
    RecoveryPolicyAdapter,
    RecoveryDecision,
)
from core.injection.injection_engine import InjectionEngine
from core.telemetry.cache_collector import CacheTelemetryCollector
from core.execution.scorers.abc import ScorerABC
from core.execution.tools.abc import ToolProviderABC, ToolDefinition, ToolExecutionContext
from core.execution.tools.selector_abc import ToolSelectorABC
from core.execution.frameworks.abc import FrameworkAdapterABC, FrameworkResult
from core.execution.outputs.abc import OutputAdapterABC

# Optional: StateReuseEvent and CacheStateSnapshot may live in extensions.
try:
    from core.telemetry.cache_collector import StateReuseEvent, CacheStateSnapshot
except ImportError:
    StateReuseEvent = None          # type: ignore[assignment,misc]
    CacheStateSnapshot = None       # type: ignore[assignment,misc]

__all__ = [
    "RetryPolicyAdapter",
    "RecoveryPolicyAdapter",
    "RecoveryDecision",
    "InjectionEngine",
    "CacheTelemetryCollector",
    "StateReuseEvent",
    "CacheStateSnapshot",
    "ScorerABC",
    "ToolProviderABC",
    "ToolDefinition",
    "ToolExecutionContext",
    "ToolSelectorABC",
    "FrameworkAdapterABC",
    "FrameworkResult",
    "OutputAdapterABC",
]
