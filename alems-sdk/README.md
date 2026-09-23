# alems-sdk

Stable facade for A-LEMS plugin authors.
Version 0.9.0 (pre Gate F).
Gate F sets 1.0.0 and freezes the contract.

Install: `pip install -e alems-sdk`

Plugin authors import only from `alems_sdk`.
Importing `core` directly is a conformance failure (INV-14).

---

## Re-export source table

Every symbol is the identical object as its core source (verified by `tests/test_sdk_reexports.py`).

| SDK module | Symbol | Core source file |
|---|---|---|
| alems_sdk.measurement | BaseReader | core/readers/interfaces.py |
| alems_sdk.measurement | EnergyReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | CPUReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | ThermalReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | ThermalReaderV2ABC | core/readers/interfaces.py |
| alems_sdk.measurement | CoolingReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | TurbostatReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | MSRReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | SchedulerMonitorABC | core/readers/interfaces.py |
| alems_sdk.measurement | DiskReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | NICReaderABC | core/readers/interfaces.py |
| alems_sdk.measurement | NormalizedEnergyReading | core/models/normalized_energy_reading.py |
| alems_sdk.measurement | PlatformAdapterABC | core/platform/adapter.py |
| alems_sdk.measurement | HardwareFingerprint | core/platform/adapter.py |
| alems_sdk.measurement | MeterInventory | core/platform/adapter.py |
| alems_sdk.serving | ServingEngineAdapter | core/serving/serving_adapter.py |
| alems_sdk.serving | ServingCapabilities | core/serving/serving_adapter.py |
| alems_sdk.serving | RequestMetrics | core/serving/serving_adapter.py |
| alems_sdk.serving | CacheState | core/serving/serving_adapter.py |
| alems_sdk.serving | ExpertTierState | core/serving/serving_adapter.py |
| alems_sdk.serving | QueueState | core/serving/serving_adapter.py |
| alems_sdk.serving | TokenRateState | core/serving/serving_adapter.py |
| alems_sdk.serving | EngineInfo | core/serving/serving_adapter.py |
| alems_sdk.harness | RetryPolicyAdapter | core/retry/retry_adapter.py |
| alems_sdk.harness | RecoveryPolicyAdapter | core/recovery/recovery_adapter.py |
| alems_sdk.harness | RecoveryDecision | core/recovery/recovery_adapter.py |
| alems_sdk.harness | InjectionEngine | core/injection/injection_engine.py |
| alems_sdk.harness | CacheTelemetryCollector | core/telemetry/cache_collector.py |
| alems_sdk.harness | StateReuseEvent | core/telemetry/cache_collector.py |
| alems_sdk.harness | CacheStateSnapshot | core/telemetry/cache_collector.py |
| alems_sdk.harness | ScorerABC | core/execution/scorers/abc.py |
| alems_sdk.harness | ToolProviderABC | core/execution/tools/abc.py |
| alems_sdk.harness | ToolDefinition | core/execution/tools/abc.py |
| alems_sdk.harness | ToolExecutionContext | core/execution/tools/abc.py |
| alems_sdk.harness | ToolSelectorABC | core/execution/tools/selector_abc.py |
| alems_sdk.harness | FrameworkAdapterABC | core/execution/frameworks/abc.py |
| alems_sdk.harness | FrameworkResult | core/execution/frameworks/abc.py |
| alems_sdk.harness | OutputAdapterABC | core/execution/outputs/abc.py |
| alems_sdk.output | OutputAdapterABC | core/execution/outputs/abc.py |
| alems_sdk.output | ExportResult | core/execution/outputs/abc.py |
| alems_sdk.persistence | DatabaseInterface | core/database/base.py |
| alems_sdk.persistence | DatabaseError | core/database/base.py |
| alems_sdk.persistence | ExtensionABC | core/extensions/abc.py |
| alems_sdk.persistence | PostRunPayload | core/extensions/abc.py |
| alems_sdk.types | Fidelity | (new, 39.1) |
| alems_sdk.types | CoverageState | (new, 39.1) |
| alems_sdk.types | IsolationLevel | (new, 39.1) |
| alems_sdk.types | ExtensionFamily | (new, 39.1) |
| alems_sdk.version | SDK_VERSION | (new, 39.1) |
| alems_sdk.version | SUPPORTED_RUNTIME_RANGE | (new, 39.1) |
| alems_sdk.conformance | run_conformance | (new, 39.1 skeleton) |
