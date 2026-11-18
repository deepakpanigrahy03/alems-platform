#!/usr/bin/env python3
"""
thermal_profile_reload.py: reload the thermal_exposure machine profile into
te_channel and te_sensor.

The extension loads the profile only on its first activation on a machine.
Run this after changing config/machine_profiles/<profile>.yaml. Writes go
through the existing writer for the resolved store (never a literal path);
a rejected profile writes nothing and lists every problem.

Usage (repo root, venv active):
    python3 scripts/tools/thermal_profile_reload.py            # profile from app_settings.yaml
    python3 scripts/tools/thermal_profile_reload.py --check    # validate only, write nothing
"""

import argparse
import logging
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.storage.inprocess_writer import InProcessWriter          # noqa: E402
from core.storage.resolver import resolve_store                    # noqa: E402
from extensions.thermal_exposure import profile_loader as pl       # noqa: E402
from extensions.thermal_exposure.extension import _PROFILE_DIR, _configured_profile  # noqa: E402

logger = logging.getLogger("thermal_profile_reload")


def main() -> int:
    """Validate and (unless --check) write the configured profile."""
    ap = argparse.ArgumentParser(description="Reload the thermal_exposure machine profile")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    name = _configured_profile()
    if not name:
        logger.error("no thermal_exposure.machine_profile in config/app_settings.yaml")
        return 1
    path = os.path.join(str(_PROFILE_DIR), name + ".yaml")
    writer = InProcessWriter(resolve_store())
    writer.open()
    try:
        execute = pl.resolve_execute(writer)
        profile, sha = pl.load_profile_file(path)
        # hostname is only the thermal_zones lookup key; it is never stored
        crow, srow = pl.build_rows(profile, sha, pl.read_catalog(execute, socket.gethostname().lower()))
        if args.check:
            logger.info("profile %s valid: %d channels, %d sensors (nothing written)", name, len(crow), len(srow))
            return 0
        pl.write_rows(execute, profile["profile_id"], crow, srow)
        pl._commit(writer)
    except pl.ProfileError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        writer.close()
    logger.info("profile %s reloaded", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
