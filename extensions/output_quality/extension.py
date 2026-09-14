"""
================================================================================
OUTPUT QUALITY EXTENSION — Native ExtensionABC Implementation
================================================================================

PURPOSE:
    Records LLM-as-judge quality scores alongside energy measurements.
    Enables the qEpG (quality Energy per Goal) metric: joint measurement of
    inference energy and answer quality in one platform.

    This is the first extension built natively using ExtensionABC.
    It validates the full 35D extension system:
      - Extension migration isolation (tables created only when active)
      - Post-run callback dispatch from experiment_runner.py
      - PostRunPayload read-only contract
      - Per-machine activation via app_settings.yaml

OBSERVER ENERGY PROPERTY:
    The quality scorer (LLM-as-judge) runs inside on_post_run(), which is
    called AFTER core commits energy_uj. The scorer's own energy consumption
    does not contaminate the inference measurement — it is causally
    downstream. This is the Observer Energy separation described in the
    A-LEMS research contribution framing.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from core.extensions.abc import ExtensionABC, PostRunPayload

logger = logging.getLogger(__name__)


class OutputQualityExtension(ExtensionABC):
    """
    Records quality evaluation results alongside core energy measurements.

    In Phase 1 (this chunk), the extension scaffolding is complete and
    the DB write path is wired. The actual LLM-as-judge scorer call is
    a stub — it will be implemented in chunk 8.5C which builds the
    full quality scoring pipeline.

    The extension is fully activatable, runs its migration, and writes
    placeholder rows so all infrastructure can be verified end-to-end
    before the scorer logic lands.
    """

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        """Return stable identity — must match [extensions] active entry."""
        return "output_quality"

    def get_version(self) -> str:
        """Return extension version string."""
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        """
        Return path to this extension's SQL migration files.

        Uses the migrations/ directory shipped alongside this file.
        For pip-installed packages, this resolves correctly because
        __file__ is inside the installed package directory.
        """
        return Path(__file__).parent / "migrations"

    def get_tables(self) -> List[str]:
        """Return tables this extension owns via its migrations."""
        return ["output_quality", "run_quality"]

    def get_config_schema(self) -> Dict:
        """
        Declare config keys read from [plugins.output_quality] in app_settings.yaml.

        Returns:
            Schema dict for config validation at activation time.
        """
        return {
            "judge_model": {
                "type": str,
                "default": None,
                "description": (
                    "Model identifier used for LLM-as-judge scoring. "
                    "Must match a provider entry in config/models.json. "
                    "When None, quality scoring is skipped (no rows written)."
                ),
            },
            "judge_temperature": {
                "type": float,
                "default": 0.0,
                "description": "Temperature for judge model calls. 0.0 = deterministic.",
            },
            "score_on_failed_runs": {
                "type": bool,
                "default": False,
                "description": "Whether to attempt scoring runs with status != 'completed'.",
            },
        }

    def on_activate(self, db: object) -> None:
        """
        Called once when this extension is first activated on a machine.

        Tables already exist (migrations ran before this is called).
        No seed data required for output_quality.
        """
        logger.info("output_quality extension activated — qEpG scoring enabled")

    def on_deactivate(self, db: object) -> None:
        """
        Called when this extension is removed from [extensions] active.

        Historical quality scores in output_quality and run_quality
        are preserved. No data is deleted.
        """
        logger.info(
            "output_quality extension deactivated — "
            "historical quality scores preserved in output_quality table"
        )

    def on_post_run(self, payload: PostRunPayload) -> None:
        """
        Called after every core run is committed.

        Reads the run result and (when scorer is configured) calls the
        LLM-as-judge scorer to evaluate answer quality. Writes the
        score and scorer energy to output_quality and run_quality.

        In Phase 1 (chunk 35D), the scorer call is a stub.
        Full scorer implementation lands in chunk 8.5C.

        The scorer runs AFTER core commits energy_uj. This is the
        Observer Energy separation: the scorer's energy does not
        contaminate the inference measurement.

        Args:
            payload: Frozen snapshot of the completed run.
        """
        # Skip failed or timed-out runs unless configured otherwise.
        # Failed runs have unreliable energy_uj values.
        if payload.status != "completed":
            logger.debug(
                "output_quality: skipping run_id=%d status=%s",
                payload.run_id,
                payload.status,
            )
            return

        try:
            self._record_quality_stub(payload)
        except Exception as exc:
            # Never propagate to experiment_runner.
            # The core run record is committed and safe.
            logger.error(
                "output_quality: on_post_run failed for run_id=%d: %s",
                payload.run_id,
                exc,
            )

    def _record_quality_stub(self, payload: PostRunPayload) -> None:
        """
        Write a placeholder quality row for this run.

        Phase 1 stub: records that quality scoring was attempted with
        score=NULL. Chunk 8.5C replaces this with the actual scorer call
        and fills score and scorer_energy_uj with real values.

        Args:
            payload: Frozen run snapshot from the extension manager.
        """
        conn = payload.db.db.conn if hasattr(payload.db, "db") else payload.db.conn

        # Insert a placeholder row into output_quality.
        # score=NULL means "scoring not yet implemented" in Phase 1.
        # scorer_energy_uj=NULL means the scorer did not run yet.
        conn.execute(
            """
            INSERT OR IGNORE INTO output_quality
                (run_id, task_id, scorer_name, score, scorer_energy_uj, evaluated_at)
            VALUES (?, NULL, 'stub_phase1', NULL, NULL, datetime('now'))
            """,
            (payload.run_id,),
        )

        # Insert or update the run_quality summary row.
        conn.execute(
            """
            INSERT OR REPLACE INTO run_quality
                (run_id, avg_score, scorer_count, total_scorer_energy_uj, computed_at)
            VALUES (?, NULL, 0, 0, datetime('now'))
            """,
            (payload.run_id,),
        )

        conn.commit()
        logger.debug(
            "output_quality: stub row written for run_id=%d "
            "(full scorer wired in chunk 8.5C)",
            payload.run_id,
        )
