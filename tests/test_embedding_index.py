#!/usr/bin/env python3
"""
================================================================================
TESTS: ToolEmbeddingIndex  —  tests/test_embedding_index.py
================================================================================
SPEC 35I v2.1. Proves the caching invariant with a real call-counting
fake embed function — not "the code returned successfully," but
"the cache was actually consulted instead of recomputing."

Run: pytest tests/test_embedding_index.py -v
================================================================================
"""

from dataclasses import dataclass
from typing import Any, Dict

import pytest

from core.execution.tools.embedding_index import (
    EmbeddingDimensionMismatchError,
    ToolEmbeddingIndex,
)


@dataclass
class FakeToolDef:
    """Minimal stand-in for ToolDefinition — only needs .name/.description/.parameters."""
    name: str
    description: str
    parameters: Dict[str, Any]


class CountingFakeEmbedder:
    """
    Deterministic, call-counted fake embedding function — NOT the same
    vector for every input (v2.1 review's explicit requirement). Vector
    is derived from the input string's hash, so identical inputs always
    produce identical vectors, and different inputs reliably differ.
    """

    def __init__(self, dimension: int = 4):
        self.call_count = 0
        self.dimension = dimension

    def __call__(self, text: str):
        self.call_count += 1
        h = abs(hash(text))
        return [((h >> (8 * i)) % 256) / 255.0 for i in range(self.dimension)]


def _provider_identity_fn(tool):
    return "builtin"


def _make_tools():
    return [
        FakeToolDef("calculator", "Evaluate a math expression", {"type": "object"}),
        FakeToolDef("database_query", "Run a read-only SELECT query", {"type": "object"}),
        FakeToolDef("file_processor", "Read/write files", {"type": "object"}),
        FakeToolDef("web_search", "Query search stub", {"type": "object"}),
        FakeToolDef("code_executor", "Run sandboxed Python", {"type": "object"}),
        FakeToolDef("api_query", "HTTP GET to stub API", {"type": "object"}),
    ]


def test_first_call_embeds_all_six():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    result = index.get_or_compute(_make_tools(), _provider_identity_fn)

    assert result.tools_embedded_count == 6
    assert result.cache_hit is False
    assert embedder.call_count == 6


def test_second_call_same_tools_zero_new_embeddings():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    index.get_or_compute(_make_tools(), _provider_identity_fn)
    assert embedder.call_count == 6

    result2 = index.get_or_compute(_make_tools(), _provider_identity_fn)

    assert result2.tools_embedded_count == 0
    assert result2.cache_hit is True
    assert embedder.call_count == 6  # UNCHANGED — the actual proof of reuse


def test_one_tool_definition_changes_exactly_one_reembedding():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    index.get_or_compute(_make_tools(), _provider_identity_fn)
    assert embedder.call_count == 6

    tools = _make_tools()
    tools[1] = FakeToolDef(
        "database_query", "Run a read-only SELECT query with joins", {"type": "object"},
    )
    result = index.get_or_compute(tools, _provider_identity_fn)

    assert result.tools_embedded_count == 1  # exactly one, not all six
    assert result.cache_hit is False
    assert embedder.call_count == 7  # 6 + 1, not 12


def test_embedding_model_change_invalidates_all():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    index.get_or_compute(_make_tools(), _provider_identity_fn)
    assert embedder.call_count == 6

    index_v2 = ToolEmbeddingIndex(embedder, "fake-model", "2.0")  # version bump
    result = index_v2.get_or_compute(_make_tools(), _provider_identity_fn)

    assert result.tools_embedded_count == 6  # nothing reused across model versions
    assert embedder.call_count == 12


def test_dimension_mismatch_fails_loudly():
    embedder = CountingFakeEmbedder(dimension=4)
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")

    # Manually poison the cache with a wrong-dimension entry to simulate
    # a genuinely inconsistent state (e.g. corrupted persisted cache).
    tools = _make_tools()
    key_tool = tools[0]
    identity = index.tool_identity("builtin", key_tool.name)
    def_hash = index.tool_definition_hash(key_tool.name, key_tool.description, key_tool.parameters)
    index._cache[(identity, def_hash, "fake-model", "1.0")] = ([0.1, 0.2], 2)  # wrong dim

    with pytest.raises(EmbeddingDimensionMismatchError):
        index.get_or_compute(tools, _provider_identity_fn)


def test_same_definition_same_model_is_cache_hit():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    tools = _make_tools()
    index.get_or_compute(tools, _provider_identity_fn)

    result = index.get_or_compute(list(tools), _provider_identity_fn)  # new list, same content

    assert result.cache_hit is True
    assert result.tools_embedded_count == 0


def test_different_tool_definition_is_cache_miss():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    index.get_or_compute(_make_tools(), _provider_identity_fn)

    tools = _make_tools()
    tools[0] = FakeToolDef("calculator", "A totally different description", {"type": "object"})
    result = index.get_or_compute(tools, _provider_identity_fn)

    assert result.cache_hit is False
    assert result.tools_embedded_count == 1


def test_mixed_five_cached_one_stale_exactly_one_reembedding():
    embedder = CountingFakeEmbedder()
    index = ToolEmbeddingIndex(embedder, "fake-model", "1.0")
    index.get_or_compute(_make_tools(), _provider_identity_fn)
    assert embedder.call_count == 6

    tools = _make_tools()
    tools[3] = FakeToolDef("web_search", "Search with a new parameter", {"type": "object", "x": 1})
    result = index.get_or_compute(tools, _provider_identity_fn)

    assert result.tools_embedded_count == 1  # only web_search, not all 6
    assert embedder.call_count == 7


def test_canonicalization_ignores_dict_key_order():
    """v2.1: dict key ordering must never produce different hashes for
    semantically identical schemas."""
    h1 = ToolEmbeddingIndex.tool_definition_hash(
        "calculator", "desc", {"a": 1, "b": 2},
    )
    h2 = ToolEmbeddingIndex.tool_definition_hash(
        "calculator", "desc", {"b": 2, "a": 1},
    )
    assert h1 == h2
