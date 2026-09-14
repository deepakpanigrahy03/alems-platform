#!/usr/bin/env python3
"""
NVIDIA Grace Platform Adapter  —  core/platform/adapters/nvidia_grace.py

Wraps NVIDIAGraceDetector from scripts/detect_hardware.py.
Handles GN100 and any future NVIDIA Grace-based server.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"
_PROVISION_SH = str(_SCRIPTS_DIR / "platforms" / "nvidia_grace" / "provision.sh")


class NVIDIAGraceAdapter(PlatformAdapterABC):
    """Platform adapter for NVIDIA Grace GB10 (GN100, aarch64 Linux)."""

    PLATFORM_CLASS: str = "nvidia_grace"
    PRIORITY:       int = 100

    def detect(self) -> Optional[Dict[str, Any]]:
        """
        Return hw_config if this machine is an NVIDIA Grace CPU.
        Delegates to NVIDIAGraceDetector.detect() from detect_hardware.py.
        Returns None on non-Grace machines or any error.
        """
        try:
            import platform as _platform
            if _platform.system() != "Linux" or _platform.machine() != "aarch64":
                return None
            # Fast pre-check before loading full detector
            from scripts.detect_hardware import NVIDIAGraceDetector, _is_nvidia_grace
            if not _is_nvidia_grace():
                return None
            detector = NVIDIAGraceDetector(verbose=False)
            return detector.detect()
        except Exception as exc:
            logger.debug("NVIDIAGraceAdapter.detect(): %s", exc)
            return None

    def provision(self) -> ProvisionResult:
        """Run GN100 provision.sh for deps and permissions."""
        result = ProvisionResult(status="success")
        for sub in ("deps", "permissions"):
            step = self._run_provision_script(_PROVISION_SH, sub)
            result.add_step(step.name, step.status, step.message)
            if step.status == "failed":
                result.status = "partial"
        return result

    def verify(self) -> VerificationResult:
        """Run GN100 hardware verification via verify_hardware.run_checks()."""
        try:
            from pathlib import Path as _Path
            import json
            hw_config_path = _Path("config/hw_config.json")
            if hw_config_path.exists():
                with open(hw_config_path) as f:
                    hw_config = json.load(f)
            else:
                hw_config = self.detect() or {}
            return self._run_verify_checks(hw_config)
        except Exception as exc:
            logger.error("NVIDIAGraceAdapter.verify(): %s", exc)
            result = VerificationResult(passed=False)
            result.add_check("verify", "Hardware verification", False, "critical", str(exc))
            return result
