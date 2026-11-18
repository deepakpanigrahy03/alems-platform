#!/usr/bin/env python3
"""
thermal_value_change_probe.py: observed value-change interval per temperature sensor.

Reads every sysfs thermal zone and the GPU temperature (NVML, when present)
as fast as possible for --seconds, round-robin, and prints one JSON result
per sensor: probe duration, reads, value changes, observed intervals,
p10/p50/p90 and resolution.

Evidence only: never sets a polling rate and writes to no store. Output goes
to stdout, or to --out (a scratch file you choose; never a store path).

Usage (repo root, venv active):
    python3 scripts/tools/thermal_value_change_probe.py --seconds 60 --out /tmp/te_probe.json
"""

import argparse
import glob
import json
import logging
import os
import re
import sys
from typing import Callable, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from extensions.thermal_exposure.value_change_probe import observe_value_changes  # noqa: E402

logger = logging.getLogger("thermal_value_change_probe")

SYSFS_ZONES = "/sys/class/thermal/thermal_zone*"   # Linux thermal ABI
ZONE_RESOLUTION_C = 0.001                          # sysfs temp is in millidegrees
GPU_RESOLUTION_C = 1.0                             # NVML GPU temperature is whole degrees


def _zone_reader(path: str) -> Callable[[], Optional[float]]:
    """Reader for one zone temp file (millidegrees to C); None on failure."""
    def read() -> Optional[float]:
        with open(path) as fh:
            return int(fh.read().strip()) / 1000.0
    return read


def _zones() -> Tuple[Dict[str, Callable], Dict[str, float]]:
    """All readable zones, named zone<N>_<type>, in numeric order."""
    readers, res = {}, {}
    dirs = sorted(glob.glob(SYSFS_ZONES), key=lambda d: int(re.sub(r"\D", "", os.path.basename(d)) or 0))
    for d in dirs:
        temp = os.path.join(d, "temp")
        if not os.access(temp, os.R_OK):
            logger.warning("zone %s: temp not readable, skipped", d)
            continue
        with open(os.path.join(d, "type")) as fh:
            ztype = fh.read().strip()
        name = "%s_%s" % (os.path.basename(d).replace("thermal_", ""), ztype)
        readers[name], res[name] = _zone_reader(temp), ZONE_RESOLUTION_C
    return readers, res


def _gpu() -> Tuple[Dict[str, Callable], Dict[str, float]]:
    """GPU 0 temperature via NVML if available; empty otherwise."""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    except Exception as exc:
        logger.warning("GPU temperature not probed: NVML unavailable (%s)", exc)
        return {}, {}

    def read() -> Optional[float]:
        return float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
    return {"gpu0_temp": read}, {"gpu0_temp": GPU_RESOLUTION_C}


def main() -> int:
    """Run the probe and print or save the per-sensor results."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", default=None, help="scratch JSON file (not a store)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    readers, res = _zones()
    g_read, g_res = _gpu()
    readers.update(g_read)
    res.update(g_res)
    if not readers:
        logger.error("no readable temperature sensor found")
        return 1
    logger.info("probing %d sensors for %.0f s: %s", len(readers), args.seconds, ", ".join(readers))
    result = observe_value_changes(readers, args.seconds, res)
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        logger.info("results written to %s", args.out)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
