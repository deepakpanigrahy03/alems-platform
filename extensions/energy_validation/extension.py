"""
================================================================================
ENERGY VALIDATION EXTENSION — Native ExtensionABC Implementation
================================================================================

PURPOSE:
    Owns the energy_validation namespace: per run measurement validity
    records, starting with energy_validation_dcgm_coverage (GPU energy
    sampler readiness, coverage of the run window, interior gaps, cap).

WHY on_post_run() IS A DOCUMENTED NO-OP:
    The extension hook fires only on the paired path (save_pair). Coverage
    must exist for every execution path (save_pair, save_single,
    execute_goal; rule EPS-1), so the row is written by
    RunPersistenceService._insert_dcgm_coverage(), which all three paths
    call, and only when this extension's table exists. This extension
    provides activation, migration ownership and table registration.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from alems_sdk.extensions import ExtensionABC, PostRunPayload

logger = logging.getLogger(__name__)


class EnergyValidationExtension(ExtensionABC):
    """
    Owns energy_validation tables. Activation lifecycle only; core
    persistence is the write path (see module docstring).
    """

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        """Return stable identity; must match the [extensions] active entry."""
        return "energy_validation"

    def get_version(self) -> str:
        """Extension version recorded in extension_registry."""
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        """
        Path to this extension's SQL migrations, shipped beside this module
        (same layout as tool_selection); the migrate tool applies the copy in
        migrations/extensions/energy_validation/.
        """
        return Path(__file__).parent / "migrations"

    def get_tables(self) -> List[str]:
        """Tables owned by this namespace."""
        return ["energy_validation_dcgm_coverage"]

    @classmethod
    def get_config_schema(cls) -> Dict:
        """No configuration keys: thresholds are versioned code constants."""
        return {"type": "object", "properties": {}, "additionalProperties": False}

    def on_activate(self, db: object) -> None:
        """Tables already exist (migrations ran first). No seed data needed."""
        logger.info("energy_validation extension activated: "
                    "DCGM coverage rows will be written per run")

    def on_deactivate(self, db: object) -> None:
        """Historical coverage rows are preserved; new runs stop writing them."""
        logger.info("energy_validation extension deactivated: "
                    "historical coverage rows preserved")

    def on_post_run(self, payload: PostRunPayload) -> None:
        """Documented no-op: core persistence writes the row on every path."""
        logger.debug("energy_validation: on_post_run no-op for run_id=%d "
                     "(coverage written by run persistence)", payload.run_id)
