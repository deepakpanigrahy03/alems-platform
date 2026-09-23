# tests/test_sdk_reexports.py
# Acceptance test 1 for SPEC_39_1 section 10:
# every SDK export must be the identical object as its core source.
# Run with: venv/bin/python -m pytest tests/test_sdk_reexports.py -v
import pytest


def _same(sdk_obj, core_obj, name: str) -> None:
    """Assert sdk_obj is core_obj (not just equal, identical)."""
    assert sdk_obj is core_obj, (
        f"alems_sdk re-export mismatch for {name}: "
        f"sdk={sdk_obj!r} core={core_obj!r}"
    )


def test_measurement_reexports():
    from core.readers.interfaces import (
        BaseReader, EnergyReaderABC, CPUReaderABC,
        ThermalReaderABC, DiskReaderABC, NICReaderABC,
    )
    from core.models.normalized_energy_reading import NormalizedEnergyReading
    from core.platform.adapter import PlatformAdapterABC

    import alems_sdk.measurement as m
    _same(m.BaseReader, BaseReader, "BaseReader")
    _same(m.EnergyReaderABC, EnergyReaderABC, "EnergyReaderABC")
    _same(m.CPUReaderABC, CPUReaderABC, "CPUReaderABC")
    _same(m.ThermalReaderABC, ThermalReaderABC, "ThermalReaderABC")
    _same(m.DiskReaderABC, DiskReaderABC, "DiskReaderABC")
    _same(m.NICReaderABC, NICReaderABC, "NICReaderABC")
    _same(m.NormalizedEnergyReading, NormalizedEnergyReading, "NormalizedEnergyReading")
    _same(m.PlatformAdapterABC, PlatformAdapterABC, "PlatformAdapterABC")


def test_serving_reexports():
    from core.serving.serving_adapter import (
        ServingEngineAdapter, ServingCapabilities, RequestMetrics,
        CacheState, ExpertTierState, QueueState, TokenRateState, EngineInfo,
    )
    import alems_sdk.serving as s
    _same(s.ServingEngineAdapter, ServingEngineAdapter, "ServingEngineAdapter")
    _same(s.ServingCapabilities, ServingCapabilities, "ServingCapabilities")
    _same(s.RequestMetrics, RequestMetrics, "RequestMetrics")
    _same(s.CacheState, CacheState, "CacheState")
    _same(s.ExpertTierState, ExpertTierState, "ExpertTierState")
    _same(s.QueueState, QueueState, "QueueState")
    _same(s.TokenRateState, TokenRateState, "TokenRateState")
    _same(s.EngineInfo, EngineInfo, "EngineInfo")


def test_harness_reexports():
    from core.retry.retry_adapter import RetryPolicyAdapter
    from core.recovery.recovery_adapter import RecoveryPolicyAdapter, RecoveryDecision
    from core.injection.injection_engine import InjectionEngine
    from core.telemetry.cache_collector import CacheTelemetryCollector
    from core.execution.scorers.abc import ScorerABC
    from core.execution.tools.abc import ToolProviderABC, ToolDefinition, ToolExecutionContext
    from core.execution.tools.selector_abc import ToolSelectorABC
    from core.execution.frameworks.abc import FrameworkAdapterABC, FrameworkResult
    from core.execution.outputs.abc import OutputAdapterABC

    import alems_sdk.harness as h
    _same(h.RetryPolicyAdapter, RetryPolicyAdapter, "RetryPolicyAdapter")
    _same(h.RecoveryPolicyAdapter, RecoveryPolicyAdapter, "RecoveryPolicyAdapter")
    _same(h.RecoveryDecision, RecoveryDecision, "RecoveryDecision")
    _same(h.InjectionEngine, InjectionEngine, "InjectionEngine")
    _same(h.CacheTelemetryCollector, CacheTelemetryCollector, "CacheTelemetryCollector")
    _same(h.ScorerABC, ScorerABC, "ScorerABC")
    _same(h.ToolProviderABC, ToolProviderABC, "ToolProviderABC")
    _same(h.ToolDefinition, ToolDefinition, "ToolDefinition")
    _same(h.ToolExecutionContext, ToolExecutionContext, "ToolExecutionContext")
    _same(h.ToolSelectorABC, ToolSelectorABC, "ToolSelectorABC")
    _same(h.FrameworkAdapterABC, FrameworkAdapterABC, "FrameworkAdapterABC")
    _same(h.FrameworkResult, FrameworkResult, "FrameworkResult")
    _same(h.OutputAdapterABC, OutputAdapterABC, "OutputAdapterABC")


def test_persistence_reexports():
    from core.database.base import DatabaseInterface, DatabaseError
    from core.extensions.abc import ExtensionABC, PostRunPayload

    import alems_sdk.persistence as p
    _same(p.DatabaseInterface, DatabaseInterface, "DatabaseInterface")
    _same(p.DatabaseError, DatabaseError, "DatabaseError")
    _same(p.ExtensionABC, ExtensionABC, "ExtensionABC")
    _same(p.PostRunPayload, PostRunPayload, "PostRunPayload")


def test_types_importable():
    # New enums; no core counterpart to compare against.
    from alems_sdk.types import Fidelity, CoverageState, IsolationLevel, ExtensionFamily
    assert Fidelity.MEASURED.value == "MEASURED"
    assert Fidelity.CALCULATED.value == "CALCULATED"
    assert Fidelity.INFERRED.value == "INFERRED"
    assert Fidelity.LIMITED.value == "LIMITED"
    assert CoverageState.FILLED.value == "filled"
    assert IsolationLevel.EXCLUSIVE.value == "exclusive"
    assert ExtensionFamily.MEASUREMENT.value == "measurement"


def test_version():
    from alems_sdk.version import SDK_VERSION, SUPPORTED_RUNTIME_RANGE
    from packaging.version import Version
    assert Version(SDK_VERSION) < Version("1.0.0"), "SDK_VERSION must be pre-1.0 until Gate F"
    assert ">=1.0.0" in SUPPORTED_RUNTIME_RANGE


def test_conformance_skeleton():
    from alems_sdk.conformance import run_conformance, ConformanceReport
    report = run_conformance("test_plugin", {"name": "test_plugin"})
    assert isinstance(report, ConformanceReport)
    assert report.passed is True
    assert report.results == []
