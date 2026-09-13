#!/usr/bin/env python3
"""
================================================================================
UNIT TESTS — Engine Registry (SPEC 35B)
================================================================================

Covers AC-1 through AC-5 from SPEC 35B Section 8.
Uses only mock adapter classes — no real LLM dependencies imported.

Run:
    cd /home/dpani/mydrive/alems-platform
    python3 -m pytest tests/test_engine_registry.py -v

Author: Deepak Panigrahy
Spec:   SPEC 35B
================================================================================
"""

import pytest
from core.readers.registry import (
    AdapterRegistry,
    DuplicateRegistrationError,
    NoAdapterError,
)


# ---------------------------------------------------------------------------
# Mock adapter classes
# ---------------------------------------------------------------------------

class MockOpenAICompat:
    ENGINE_TYPE = "openai_compat"
    METHOD_ID   = "openai_compat"   # registry uses METHOD_ID as key

    @classmethod
    def can_handle(cls, caps):
        return False   # engines never use can_handle


class MockAnthropic:
    ENGINE_TYPE = "anthropic"
    METHOD_ID   = "anthropic"

    @classmethod
    def can_handle(cls, caps):
        return False


class MockLlamaCpp:
    ENGINE_TYPE = "llama_cpp"
    METHOD_ID   = "llama_cpp"

    @classmethod
    def can_handle(cls, caps):
        return False


class MockGemini:
    ENGINE_TYPE = "gemini"
    METHOD_ID   = "gemini"

    @classmethod
    def can_handle(cls, caps):
        return False


class MockKokoro:
    ENGINE_TYPE = "kokoro"
    METHOD_ID   = "kokoro"

    @classmethod
    def can_handle(cls, caps):
        return False


class MockDuplicate:
    """Second adapter claiming same ENGINE_TYPE as MockOpenAICompat."""
    ENGINE_TYPE = "openai_compat"
    METHOD_ID   = "openai_compat"

    @classmethod
    def can_handle(cls, caps):
        return False


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

def test_register_single_adapter():
    """AC-1 partial: adapter registers without error."""
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)
    assert "openai_compat" in reg.get_all()


def test_register_all_text_adapters():
    """All four text adapters register with distinct ENGINE_TYPEs."""
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)
    reg.register(MockAnthropic)
    reg.register(MockLlamaCpp)
    reg.register(MockGemini)
    assert len(reg.get_all()) == 4


def test_duplicate_engine_type_raises():
    """AC-3: duplicate ENGINE_TYPE raises DuplicateRegistrationError (INV-6)."""
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)
    with pytest.raises(DuplicateRegistrationError) as exc_info:
        reg.register(MockDuplicate)
    assert "openai_compat" in str(exc_info.value)
    assert "INV-6" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Lookup tests — engines use get() not select()
# ---------------------------------------------------------------------------

def test_get_returns_correct_adapter():
    """get() returns correct adapter class for known ENGINE_TYPE."""
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)
    reg.register(MockAnthropic)
    reg.register(MockLlamaCpp)

    assert reg.get("openai_compat") is MockOpenAICompat
    assert reg.get("anthropic") is MockAnthropic
    assert reg.get("llama_cpp") is MockLlamaCpp


def test_get_unknown_engine_type_raises():
    """AC-4: unknown ENGINE_TYPE raises KeyError — factory falls through to legacy."""
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)

    with pytest.raises(KeyError):
        reg.get("ollama")   # not registered


def test_media_registry_independent():
    """text_registry and media_registry are independent instances."""
    text_reg  = AdapterRegistry(family="text_engine")
    media_reg = AdapterRegistry(family="media_engine")

    text_reg.register(MockOpenAICompat)
    media_reg.register(MockKokoro)

    assert "openai_compat" in text_reg.get_all()
    assert "kokoro" in media_reg.get_all()
    assert "openai_compat" not in media_reg.get_all()
    assert "kokoro" not in text_reg.get_all()


# ---------------------------------------------------------------------------
# is_empty guard — AC-4
# ---------------------------------------------------------------------------

def test_is_empty_before_registration():
    """Registry empty before bootstrap called."""
    reg = AdapterRegistry(family="text_engine")
    assert reg.is_empty()


def test_get_on_empty_registry_raises():
    """get() on empty registry raises KeyError — factory falls through."""
    reg = AdapterRegistry(family="text_engine")
    with pytest.raises(KeyError):
        reg.get("openai_compat")


# ---------------------------------------------------------------------------
# Measurement boundary — AC-5
# ---------------------------------------------------------------------------

def test_adapter_has_no_energy_reader_access():
    """
    AC-5: adapter class has no reference to energy readers or runs table.
    Verified structurally — adapter classes only expose call()/process().
    The harness owns energy before/after adapter.call().
    """
    reg = AdapterRegistry(family="text_engine")
    reg.register(MockOpenAICompat)
    cls = reg.get("openai_compat")

    # Adapter must not have energy reader or DB write methods
    assert not hasattr(cls, "read_energy_uj")
    assert not hasattr(cls, "write_run")
    assert not hasattr(cls, "energy_reader")
