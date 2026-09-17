"""
================================================================================
TOOL SELECTION EXTENSION — Native ExtensionABC Implementation
================================================================================

PURPOSE:
    Owns the tool_selection_events table that RetrievalToolSelector
    (SPEC 35I) writes to. Provides activation lifecycle (migration
    ownership, table registration) — does NOT provide the write path.

WHY on_post_run() IS A DOCUMENTED NO-OP (SPEC 35H CR-4):
    ExtensionABC.on_post_run() fires strictly after core commits a run
    (see core/extensions/abc.py's own docstring: "no before-run or
    during-run hook exists"). Tool selection happens BEFORE the LLM
    call, inside a run that has not been committed yet — on_post_run()
    physically cannot be the write path for selection events.

    RetrievalToolSelector.select() writes to tool_selection_events
    directly, at selection time, reusing the live db connection passed
    via ToolSelectionContext.db (SPEC 35H CR-5) — never a second
    connection to the same database file.

    This is specific to this extension's timing constraint. It does
    NOT establish that all future extensions should bypass
    on_post_run() — see SPEC 35I v2 Section 7's explicit caveat against
    treating this as a general pattern.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.extensions.abc import ExtensionABC, PostRunPayload

logger = logging.getLogger(__name__)


class ToolSelectionExtension(ExtensionABC):
    """
    Owns tool_selection_events. Activation/deactivation lifecycle only —
    RetrievalToolSelector.select() is the actual write path.
    """

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        """Return stable identity — must match [extensions] active entry."""
        return "tool_selection"

    def get_version(self) -> str:
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        """
        Path to this extension's SQL migration files, shipped alongside
        this module — same pattern as output_quality's own
        get_migrations_dir(), resolves correctly for a pip-installed
        package since __file__ is inside the installed directory.
        """
        return Path(__file__).parent / "migrations"

    def get_tables(self) -> List[str]:
        return ["tool_selection_events"]

    def get_config_schema(self) -> Dict:
        """
        Config keys read from [plugins.tool_selection] in
        app_settings.yaml. embedding_model has no platform-wide
        default declared here deliberately — SPEC 35I's embedding
        library/model choice is still an open design question (v2.1
        Section 6, item 2); forcing a default here would silently
        pre-empt that decision.
        """
        return {
            "embedding_model": {
                "type": str,
                "default": None,
                "description": (
                    "Embedding model identifier used by "
                    "RetrievalToolSelector. Required for that selector "
                    "to report is_available()=True — see SPEC 35I "
                    "Section 4's capability/configuration separation."
                ),
            },
        }

    def on_activate(self, db: object) -> None:
        """Called once when first activated. Tables already exist
        (migrations ran before this is called). No seed data needed."""
        logger.info(
            "tool_selection extension activated — RetrievalToolSelector "
            "may now write to tool_selection_events"
        )

    def on_deactivate(self, db: object) -> None:
        """
        Historical tool_selection_events data is preserved — no
        deletion, matching output_quality's own on_deactivate contract.
        After deactivation, RetrievalToolSelector's configuration
        pre-flight (SPEC 35I Section 4) will fail loudly at experiment
        setup time, since the selector has nowhere to write.
        """
        logger.info(
            "tool_selection extension deactivated — historical "
            "selection events preserved in tool_selection_events table"
        )

    def on_post_run(self, payload: PostRunPayload) -> None:
        """
        Documented no-op — see module docstring. Selection already
        happened and was already recorded, before this run was even
        committed. Nothing for this hook to do.
        """
        logger.debug(
            "tool_selection: on_post_run no-op for run_id=%d "
            "(selection events, if any, were already written directly "
            "by RetrievalToolSelector.select() before this run started)",
            payload.run_id,
        )
        return
