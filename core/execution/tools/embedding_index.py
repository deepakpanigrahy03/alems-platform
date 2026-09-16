#!/usr/bin/env python3
"""
================================================================================
TOOL EMBEDDING INDEX  —  core/execution/tools/embedding_index.py
================================================================================
SPEC 35I v2.1 Section 1/2. Pure caching/staleness logic — knows nothing
about sentence-transformers, OpenAI embeddings, or any specific library.
Takes an embedding function as a dependency. The later embedding-library
decision (v2.1 Section 6, still open) becomes a plug-in choice at the
call site, never an architectural change here.

CONTRACT:
    ToolDefinition + embedding function + model identity/version
        -> ToolEmbeddingIndex.get_or_compute(tools)
        -> EmbeddingIndexResult (vectors, cache_hit, tools_embedded_count)

STALENESS KEY (v2.1 Section 1): a cached vector is reused only when
ALL of (tool identity, tool_definition_hash, embedding_model_id,
embedding_model_version) match the cached entry. Any single mismatch
means that one tool is re-embedded — never the whole catalog.

AUTHOR: Deepak Panigrahy
SPEC:   35I v2.1
================================================================================
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingIndexResult:
    """
    Returned by get_or_compute(). RetrievalToolSelector reads these
    fields directly into tool_selection_events — it never inspects
    the index's internal cache structure (v2.1 Section 1 caveat,
    "clean boundary" requirement).
    """
    vectors: List[Sequence[float]]  # aligned 1:1 with the input tools list
    cache_hit: bool                 # True only if EVERY tool was a cache hit
    tools_embedded_count: int
    embedding_dimension: Optional[int]


class EmbeddingDimensionMismatchError(Exception):
    """Raised when tool vectors in one get_or_compute() call don't
    share a single dimension — never silently truncated or padded."""


class ToolEmbeddingIndex:
    """
    In-process cache. Exact long-term storage mechanism (DB-backed,
    file-backed, etc.) is an open implementation question (v2.1
    Section 9, item 1) — this class defines the correct *behavior*,
    which any storage backend must satisfy.
    """

    def __init__(
        self,
        embed_fn: Callable[[str], Sequence[float]],
        embedding_model_id: str,
        embedding_model_version: str,
    ):
        """
        Args:
            embed_fn: text -> vector. This class never imports or
                knows about the library backing this function.
            embedding_model_id: e.g. "bge-small-en-v1.5". Part of the
                staleness key — changing this invalidates every
                previously cached vector.
            embedding_model_version: e.g. "1.0". Same role as
                embedding_model_id — kept separate per v2.1's explicit
                requirement that model identity AND version both gate
                cache validity independently.
        """
        self._embed_fn = embed_fn
        self._model_id = embedding_model_id
        self._model_version = embedding_model_version
        # key: (tool_identity, tool_definition_hash, model_id, model_version)
        #   -> (vector, dimension)
        self._cache: Dict[Tuple[str, str, str, str], Tuple[Sequence[float], int]] = {}

    @staticmethod
    def tool_identity(provider_identity: str, tool_name: str) -> str:
        """
        v2.1 Section 1: provider_identity + tool_name, kept simple —
        not overdesigned into a UUID. Caller supplies provider_identity
        (e.g. TOOL_PROVIDER_TYPE); this class does not resolve it.
        """
        return f"{provider_identity}:{tool_name}"

    @staticmethod
    def tool_definition_hash(name: str, description: str, parameters: Dict[str, Any]) -> str:
        """
        v2.1 Section 1: hash of canonicalized name + description +
        canonicalized parameter schema. json.dumps(sort_keys=True)
        ensures dict key ordering never produces different hashes for
        semantically identical schemas.
        """
        canonical = json.dumps(
            {"name": name, "description": description, "parameters": parameters},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_or_compute(
        self,
        tools: List[Any],  # List[ToolDefinition] — typed Any to avoid
                            # a hard import dependency in this module
        provider_identity_fn: Callable[[Any], str],
    ) -> EmbeddingIndexResult:
        """
        For each tool: reuse the cached vector if its identity+hash+
        model key matches, otherwise embed it fresh and cache the
        result. Never re-embeds a tool whose key is unchanged, even
        if other tools in the same call are cache misses (v2.1's
        "mixed 5 cached + 1 stale -> exactly 1 re-embedding" case).

        Raises:
            EmbeddingDimensionMismatchError: if the resulting vectors
                don't all share one dimension — fails loudly rather
                than producing a numerically meaningless similarity
                score later.
        """
        vectors: List[Sequence[float]] = []
        embedded_count = 0
        dims_seen = set()

        for tool in tools:
            identity = self.tool_identity(provider_identity_fn(tool), tool.name)
            def_hash = self.tool_definition_hash(tool.name, tool.description, tool.parameters)
            key = (identity, def_hash, self._model_id, self._model_version)

            cached = self._cache.get(key)
            if cached is not None:
                vec, dim = cached
            else:
                vec = self._embed_fn(tool.description)
                dim = len(vec)
                self._cache[key] = (vec, dim)
                embedded_count += 1
                logger.debug(
                    "ToolEmbeddingIndex: embedded '%s' (cache miss — new or "
                    "stale definition/model)", tool.name,
                )

            vectors.append(vec)
            dims_seen.add(dim)

        if len(dims_seen) > 1:
            raise EmbeddingDimensionMismatchError(
                f"Tool embeddings in this call have inconsistent dimensions: "
                f"{sorted(dims_seen)}. This means tools were embedded under "
                f"different model identities/versions without going through "
                f"a consistent staleness key — investigate before proceeding."
            )

        dimension = dims_seen.pop() if dims_seen else None
        return EmbeddingIndexResult(
            vectors=vectors,
            cache_hit=(embedded_count == 0),
            tools_embedded_count=embedded_count,
            embedding_dimension=dimension,
        )

    def cache_size(self) -> int:
        """Diagnostic only — number of distinct (tool, model) entries cached."""
        return len(self._cache)
