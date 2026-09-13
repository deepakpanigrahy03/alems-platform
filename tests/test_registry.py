#!/usr/bin/env python3
"""
================================================================================
UNIT TESTS — AdapterRegistry
================================================================================

Covers SPEC 35A acceptance criteria AC-1 through AC-7.
Uses only mock reader classes — no hardware, no real readers imported.

Key architectural rules tested:
    - Dummies are NOT in the registry (NoAdapterError -> factory handles it)
    - Ties at same PRIORITY raise ConfigurationError (INV-5)
    - Duplicate METHOD_ID raises DuplicateRegistrationError (INV-6)
    - can_handle() misbehaving does not crash selection
    - is_empty() guard works before bootstrap is called

Run:
    cd /home/dpani/mydrive/alems-platform
    python3 -m pytest tests/test_registry.py -v

Author: Deepak Panigrahy
Spec:   SPEC 35A, Section 9
================================================================================
"""

import pytest
from core.readers.registry import (
    AdapterRegistry,
    DuplicateRegistrationError,
    ConfigurationError,
    NoAdapterError,
)


# ---------------------------------------------------------------------------
# Mock PlatformCapabilities — no import from platform.py needed
# ---------------------------------------------------------------------------

class MockCaps:
    def __init__(
        self,
        os="Linux",
        arch="x86_64",
        measurement_mode="MEASURED",
        is_grace_cpu=False,
        has_spbm=False,
        has_arm_pmu=False,
        has_thermal=True,
    ):
        self.os               = os
        self.arch             = arch
        self.measurement_mode = measurement_mode
        self.is_grace_cpu     = is_grace_cpu
        self.has_spbm         = has_spbm
        self.has_arm_pmu      = has_arm_pmu
        self.has_thermal      = has_thermal


# ---------------------------------------------------------------------------
# Mock real reader classes (no dummies in registry)
# ---------------------------------------------------------------------------

class MockRAPL:
    METHOD_ID = "rapl_msr_pkg_energy"
    PRIORITY  = 100

    @classmethod
    def can_handle(cls, caps):
        return (
            caps.os == "Linux"
            and caps.arch == "x86_64"
            and caps.measurement_mode == "MEASURED"
        )


class MockSPBM:
    METHOD_ID = "spbm_pkg_v1"
    PRIORITY  = 100

    @classmethod
    def can_handle(cls, caps):
        return (
            caps.os == "Linux"
            and caps.is_grace_cpu
            and caps.has_spbm
            and caps.measurement_mode == "MEASURED"
        )


class MockIOKit:
    METHOD_ID = "iokit_power_reader"
    PRIORITY  = 100

    @classmethod
    def can_handle(cls, caps):
        return caps.os == "Darwin" and caps.measurement_mode == "MEASURED"


class MockEstimator:
    """Real measurement attempt (INFERRED mode) — goes in registry."""
    METHOD_ID = "ml_energy_estimator"
    PRIORITY  = 500

    @classmethod
    def can_handle(cls, caps):
        return caps.measurement_mode == "INFERRED"


class MockReaderA:
    """Generic reader A for priority/tie tests."""
    METHOD_ID = "reader_a"
    PRIORITY  = 100

    @classmethod
    def can_handle(cls, caps):
        return True


class MockReaderB:
    """Generic reader B — same PRIORITY as A, used for tie test."""
    METHOD_ID = "reader_b"
    PRIORITY  = 100

    @classmethod
    def can_handle(cls, caps):
        return True


class MockReaderLow:
    """Lower priority reader — loses to MockReaderA when both eligible."""
    METHOD_ID = "reader_low"
    PRIORITY  = 200

    @classmethod
    def can_handle(cls, caps):
        return True


class MockBadHandle:
    """Reader whose can_handle raises — must not crash selection."""
    METHOD_ID = "bad_handle"
    PRIORITY  = 50

    @classmethod
    def can_handle(cls, caps):
        raise RuntimeError("can_handle intentionally broken")


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_register_single_reader():
    """A reader registers without error."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    assert "rapl_msr_pkg_energy" in reg.get_all()


def test_register_multiple_readers():
    """Multiple readers with distinct METHOD_IDs register cleanly."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    reg.register(MockSPBM)
    reg.register(MockEstimator)
    assert len(reg.get_all()) == 3


def test_duplicate_method_id_raises():
    """AC-5: duplicate METHOD_ID raises DuplicateRegistrationError (INV-6)."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    with pytest.raises(DuplicateRegistrationError) as exc_info:
        reg.register(MockRAPL)
    assert "INV-6" in str(exc_info.value)
    assert "rapl_msr_pkg_energy" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Selection — happy paths
# ---------------------------------------------------------------------------

def test_rapl_selected_on_linux_x86():
    """AC-3: RAPL selected on Linux x86_64 MEASURED."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    reg.register(MockEstimator)

    caps = MockCaps(os="Linux", arch="x86_64", measurement_mode="MEASURED")
    assert reg.select(caps) is MockRAPL


def test_spbm_selected_on_grace():
    """SPBM selected on Linux aarch64 Grace with SPBM available."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    reg.register(MockSPBM)
    reg.register(MockEstimator)

    caps = MockCaps(
        os="Linux", arch="aarch64", measurement_mode="MEASURED",
        is_grace_cpu=True, has_spbm=True,
    )
    assert reg.select(caps) is MockSPBM


def test_iokit_selected_on_darwin():
    """IOKit selected on macOS MEASURED."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    reg.register(MockIOKit)
    reg.register(MockEstimator)

    caps = MockCaps(os="Darwin", arch="arm64", measurement_mode="MEASURED")
    assert reg.select(caps) is MockIOKit


def test_estimator_selected_on_inferred():
    """EnergyEstimator selected when measurement_mode is INFERRED."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    reg.register(MockEstimator)

    caps = MockCaps(os="Linux", arch="x86_64", measurement_mode="INFERRED")
    assert reg.select(caps) is MockEstimator


def test_priority_ordering():
    """Lower PRIORITY wins when multiple readers eligible."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockReaderLow)   # PRIORITY=200
    reg.register(MockReaderA)     # PRIORITY=100

    caps = MockCaps()
    assert reg.select(caps) is MockReaderA


# ---------------------------------------------------------------------------
# Selection — error paths
# ---------------------------------------------------------------------------

def test_no_match_raises_no_adapter_error():
    """
    AC-7 / architectural rule: no dummy in registry.
    When no real reader matches, NoAdapterError is raised.
    Factory catches this and returns a dummy explicitly tagged LIMITED.
    """
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)    # Linux x86_64 only
    reg.register(MockSPBM)    # Grace only

    # Windows — neither matches, no dummy registered, NoAdapterError expected
    caps = MockCaps(os="Windows", arch="x86_64", measurement_mode="MEASURED")
    with pytest.raises(NoAdapterError):
        reg.select(caps)


def test_tie_raises_configuration_error():
    """AC-6: tie at same PRIORITY raises ConfigurationError (INV-5)."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockReaderA)   # PRIORITY=100, can_handle=True
    reg.register(MockReaderB)   # PRIORITY=100, can_handle=True

    caps = MockCaps()
    with pytest.raises(ConfigurationError) as exc_info:
        reg.select(caps)
    assert "INV-5" in str(exc_info.value)


def test_bad_can_handle_treated_as_ineligible():
    """
    A reader whose can_handle() raises is skipped.
    Selection continues with remaining readers.
    """
    reg = AdapterRegistry(family="energy")
    reg.register(MockBadHandle)   # raises in can_handle
    reg.register(MockReaderLow)   # PRIORITY=200, always True

    caps = MockCaps()
    # MockBadHandle skipped, MockReaderLow wins
    assert reg.select(caps) is MockReaderLow


# ---------------------------------------------------------------------------
# is_empty guard — AC-4
# ---------------------------------------------------------------------------

def test_is_empty_before_registration():
    """Registry is empty before bootstrap is called."""
    reg = AdapterRegistry(family="energy")
    assert reg.is_empty()


def test_is_not_empty_after_registration():
    """Registry not empty after at least one registration."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    assert not reg.is_empty()


def test_empty_registry_raises_no_adapter_error():
    """
    AC-4: factory.py guards with is_empty() before calling select().
    If guard is bypassed on empty registry, NoAdapterError fires.
    """
    reg = AdapterRegistry(family="energy")
    with pytest.raises(NoAdapterError):
        reg.select(MockCaps())


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------

def test_get_all_returns_copy():
    """Mutating get_all() result does not affect registry."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    snapshot = reg.get_all()
    snapshot["injected"] = object()
    assert "injected" not in reg.get_all()


def test_repr_contains_family_and_keys():
    """repr is useful for debugging."""
    reg = AdapterRegistry(family="energy")
    reg.register(MockRAPL)
    r = repr(reg)
    assert "energy" in r
    assert "rapl_msr_pkg_energy" in r
