"""
================================================================================
THERMAL EXPOSURE EXTENSION — Native ExtensionABC Implementation
================================================================================

PURPOSE:
    Owns the thermal_exposure namespace (all te_ tables): the power channel
    and temperature sensor registers, session tables used by the session
    recorder, and room temperature samples.

WHAT ACTIVATION DOES:
    Loads the machine profile named in config/app_settings.yaml
    (thermal_exposure: {machine_profile: <name>}) into te_channel and
    te_sensor, through the writer handle passed to on_activate(). A machine
    with no profile configured activates normally and loads no rows.
    A profile that does not match this machine's discovered hardware is
    rejected with every problem listed, and nothing is written.

WHY on_post_run() IS A DOCUMENTED NO-OP:
    Normal runs are unchanged by this extension. Session recording (one
    continuous recording across goals and pauses) is added separately and
    writes through run persistence, as energy_validation does.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
import socket
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from alems_sdk.extensions import ExtensionABC, PostRunPayload

from extensions.thermal_exposure import profile_loader

logger = logging.getLogger(__name__)

# Repository root: extensions/thermal_exposure/extension.py -> two levels up
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_FILE = _REPO_ROOT / "config" / "app_settings.yaml"
_PROFILE_DIR = _REPO_ROOT / "config" / "machine_profiles"
_SETTINGS_KEY = "thermal_exposure"

TE_TABLES = ["te_channel", "te_sensor", "te_session", "te_idle_window",
             "te_clock_anchor", "te_session_check", "te_ambient_samples"]


class ThermalExposureExtension(ExtensionABC):
    """Owns te_ tables; loads the machine profile registers on activation."""

    EXTENSION_VERSION = "1.0.0"

    def get_name(self) -> str:
        """Return stable identity; must match the [extensions] active entry."""
        return "thermal_exposure"

    def get_version(self) -> str:
        """Extension version recorded in extension_registry."""
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        """
        Path to this extension's SQL migrations, shipped beside this module;
        the migrate tool applies the copy in migrations/extensions/thermal_exposure/.
        """
        return Path(__file__).parent / "migrations"

    def get_tables(self) -> List[str]:
        """Tables owned by this namespace."""
        return list(TE_TABLES)

    @classmethod
    def get_config_schema(cls) -> Dict:
        """One optional key: the logical machine profile name (never a hostname)."""
        return {"type": "object",
                "properties": {"machine_profile": {"type": "string"}},
                "additionalProperties": False}

    def on_activate(self, db: object) -> None:
        """
        Load the configured machine profile into te_channel and te_sensor.

        A rejected profile is logged as an error and leaves the registers
        unchanged; activation itself does not fail, so normal runs continue.
        """
        name = _configured_profile()
        if not name:
            logger.info("thermal_exposure activated: no machine_profile configured, "
                        "registers left empty")
            return
        path = _PROFILE_DIR / ("%s.yaml" % name)
        if not path.is_file():
            logger.error("thermal_exposure: machine profile file not found: %s", path)
            return
        try:
            # machine_id is only the thermal_zones lookup key; it is never stored in te_ tables
            n_ch, n_se = profile_loader.load_into_store(
                db, str(path), socket.gethostname().lower())
        except profile_loader.ProfileError as exc:
            logger.error("thermal_exposure: %s", exc)
            return
        logger.info("thermal_exposure activated: profile %s (%d channels, %d sensors)",
                    name, n_ch, n_se)

    def on_deactivate(self, db: object) -> None:
        """Registers and session rows are preserved; nothing new is written."""
        logger.info("thermal_exposure deactivated: te_ rows preserved")

    def on_post_run(self, payload: PostRunPayload) -> None:
        """Documented no-op: normal runs are unchanged by this extension."""
        logger.debug("thermal_exposure: on_post_run no-op for run_id=%d", payload.run_id)


def _configured_profile() -> Optional[str]:
    """
    Machine profile name from config/app_settings.yaml, or None.

    Read directly (not through core) so the extension imports only alems_sdk.
    """
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as fh:
            settings = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("thermal_exposure: cannot read %s (%s)", _SETTINGS_FILE, exc)
        return None
    section = settings.get(_SETTINGS_KEY) or {}
    return section.get("machine_profile") if isinstance(section, dict) else None
