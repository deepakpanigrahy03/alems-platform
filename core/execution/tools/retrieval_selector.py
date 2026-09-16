#!/usr/bin/env python3
"""
================================================================================
RETRIEVAL TOOL SELECTOR  —  core/execution/tools/retrieval_selector.py
================================================================================
SPEC 35I v2.1. Ships as an EXTERNAL plugin decision was made for the
package as a whole (v2.1 Section 6) — this file, in this session's
delivery, lives alongside the built-in selector for simplicity of
verification; packaging it as a separate alems-selector-retrieval
distribution is a build/release-process step, not a code change, and
can happen without touching this file's logic.

Embedding library: sentence-transformers, all-MiniLM-L6-v2 (local,
in-process, no network call — avoids the network-energy-attribution
problem already left unresolved for LangChain, per v2.1 Section 2.1's
own reasoning).

CONFIGURATION PRE-FLIGHT NOTE (v2.1 Section 4): is_available() below
is CAPABILITY ONLY (is sentence-transformers importable). Whether
ext-tool-selection is active in app_settings.yaml is a SEPARATE check
— done here via _is_extension_active(), a local helper mirroring
alems_migrate.py's own _read_active_extensions() reading logic
directly, since no shared "is extension X active" accessor was
confirmed to exist elsewhere in this codebase this session. This
selector's select() calls both checks explicitly and fails loudly
(v2.1 AC-I1b) rather than conflating them into one is_available()
return value.

AUTHOR: Deepak Panigrahy
SPEC:   35I v2.1
================================================================================
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import yaml

from core.execution.tools.abc import ToolDefinition
from core.execution.tools.embedding_index import ToolEmbeddingIndex
from core.execution.tools.selector_abc import (
    ToolSelectionContext, ToolSelectionResult, ToolSelectorABC,
)

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL_ID = "all-MiniLM-L6-v2"
_EMBEDDING_MODEL_VERSION = "1.0"  # sentence-transformers model card version,
                                  # bump if the pinned model ever changes


def _is_extension_active(ext_name: str) -> bool:
    """
    Mirrors alems_migrate.py's own _read_active_extensions() reading
    logic directly against config/app_settings.yaml — no shared
    accessor for "is extension X active" was confirmed to exist
    elsewhere in this codebase this session, so this is intentionally
    self-contained rather than guessing at one.
    """
    try:
        config_path = Path("config/app_settings.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        extensions_section = config.get("extensions")
        if extensions_section is None:
            return False  # legacy mode — no extensions active at all
        active = extensions_section.get("active") or []
        if isinstance(active, str):
            active = [s.strip() for s in active.split(",") if s.strip()]
        return ext_name in active
    except Exception as exc:
        logger.warning(
            "RetrievalToolSelector: could not read app_settings.yaml "
            "extensions.active: %s — treating as inactive", exc,
        )
        return False


class RetrievalToolSelector(ToolSelectorABC):
    SELECTOR_TYPE = "retrieval"

    def __init__(self):
        self._model = None  # lazy-loaded — is_available() must not
                             # force a model load just to answer a
                             # capability question
        self._index = None

    def _ensure_model_loaded(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(_EMBEDDING_MODEL_ID)
            self._index = ToolEmbeddingIndex(
                embed_fn=lambda text: self._model.encode(text).tolist(),
                embedding_model_id=_EMBEDDING_MODEL_ID,
                embedding_model_version=_EMBEDDING_MODEL_VERSION,
            )

    def select(
        self,
        available: List[ToolDefinition],
        task_context: Dict[str, Any],
        context: ToolSelectionContext,
    ) -> ToolSelectionResult:
        # v2.1 Section 1: empty catalog is an upstream integration
        # error, not a valid zero-tool selection — fail before any
        # embedding work.
        if not available:
            raise ValueError(
                "RetrievalToolSelector.select() called with an empty "
                "tool catalog — this indicates an upstream integration "
                "error (select() should never be invoked for a task "
                "that requests zero tools), not a valid selection."
            )

        # k validation (v2.1 AC-I6): requested_k and actual_k recorded
        # separately, never silently merged.
        requested_k = task_context.get("tool_selector_k", 5)
        if not isinstance(requested_k, int) or isinstance(requested_k, bool) or requested_k < 1:
            raise ValueError(
                f"tool_selector_k must be a positive integer, got {requested_k!r}"
            )
        actual_k = min(requested_k, len(available))

        if context.db is None or context.energy_reader is None:
            raise RuntimeError(
                "RetrievalToolSelector requires a live db and energy_reader "
                "in ToolSelectionContext — got None. This means the harness "
                "has not wired selector context correctly."
            )

        # v2.1 Section 4: configuration pre-flight, separate from
        # is_available()'s capability-only check.
        if not _is_extension_active("tool_selection"):
            raise RuntimeError(
                "RetrievalToolSelector is configured but 'tool_selection' "
                "is not in app_settings.yaml extensions.active — nowhere "
                "to write selection events. Add it to extensions.active "
                "and re-run 'alems dev sync' before using this selector."
            )

        self._ensure_model_loaded()

        energy_before = context.energy_reader.read_energy()
        start_ns = time.time_ns()

        task_description = task_context.get("task_description", "")
        query_vec = np.array(self._model.encode(task_description).tolist())

        provider_identity_fn = lambda tool: task_context.get("provider_identity", "builtin")
        index_result = self._index.get_or_compute(available, provider_identity_fn)

        # Dimension consistency across query vs tool vectors (v2.1 Section 2,
        # external review's dimension-mismatch requirement) — fail loudly,
        # never silently truncate/pad.
        if index_result.embedding_dimension is not None and len(query_vec) != index_result.embedding_dimension:
            raise ValueError(
                f"Query embedding dimension ({len(query_vec)}) does not "
                f"match tool embedding dimension ({index_result.embedding_dimension}) "
                f"— investigate before proceeding, do not truncate or pad."
            )

        tool_vecs = np.array(index_result.vectors)
        # Cosine similarity — plain numpy, no additional dependency.
        norms = np.linalg.norm(tool_vecs, axis=1) * np.linalg.norm(query_vec)
        norms[norms == 0] = 1e-10  # avoid divide-by-zero on a degenerate embedding
        similarities = (tool_vecs @ query_vec) / norms

        ranked_indices = np.argsort(-similarities)[:actual_k]
        selected = [available[i] for i in ranked_indices]

        energy_after = context.energy_reader.read_energy()
        duration_ns = time.time_ns() - start_ns
        selector_energy_uj = (
            energy_after - energy_before
            if energy_before is not None and energy_after is not None
            else None
        )

        self._write_selection_event(
            context, available, requested_k, actual_k, selected,
            selector_energy_uj, duration_ns, index_result,
        )

        return ToolSelectionResult(
            selected=selected,
            selector_energy_uj=selector_energy_uj,
            metadata={
                "requested_k": requested_k, "actual_k": actual_k,
                "available_count": len(available),
                "energy_provenance": "measured_interval",
                "cache_hit": index_result.cache_hit,
                "tools_embedded_count": index_result.tools_embedded_count,
            },
        )

    def _write_selection_event(
        self, context, available, requested_k, actual_k, selected,
        selector_energy_uj, duration_ns, index_result,
    ) -> None:
        """
        CR-4 (SPEC 35H): direct write, not via on_post_run() — selection
        happens before a run is committed. Reuses context.db's live
        connection, never opens a second one to the same SQLite file
        (CR-5).
        """
        import json
        try:
            context.db.execute(
                "INSERT INTO tool_selection_events "
                "(run_id, selector_type, available_count, requested_k, actual_k, "
                " selected_tool_names, selector_energy_uj, energy_provenance, "
                " duration_ns, embedding_model, embedding_dimensions, cache_hit, "
                " tools_embedded_count, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (
                    context.run_id, self.SELECTOR_TYPE, len(available),
                    requested_k, actual_k,
                    json.dumps([t.name for t in selected]),
                    selector_energy_uj, "measured_interval", duration_ns,
                    _EMBEDDING_MODEL_ID, index_result.embedding_dimension,
                    int(index_result.cache_hit), index_result.tools_embedded_count,
                ),
            )
        except Exception as exc:
            logger.error(
                "RetrievalToolSelector: failed to write tool_selection_events "
                "row: %s — selection itself still succeeded, only the "
                "energy-accounting record was lost", exc,
            )

    def get_name(self) -> str:
        return "retrieval"

    def is_available(self) -> bool:
        """
        CAPABILITY ONLY (v2.1 Section 4) — does not check
        extensions.active. See module docstring.
        """
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def get_config_schema(self) -> Dict[str, Any]:
        return {
            "tool_selector_k": {
                "type": "int", "required": False, "default": 5,
                "description": "Number of top-ranked tools to select.",
            },
        }
