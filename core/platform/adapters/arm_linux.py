#!/usr/bin/env python3
"""Generic ARM Linux Platform Adapter  —  core/platform/adapters/arm_linux.py"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)
_PROVISION_SH = str(Path(__file__).parent.parent.parent.parent / "scripts" / "platforms" / "linux_arm" / "provision.sh")


class ARMLinuxAdapter(PlatformAdapterABC):
    """Platform adapter for generic ARM Linux (non-Grace)."""

    PLATFORM_CLASS: str = "linux_arm"
    PRIORITY:       int = 200

    def detect(self) -> Optional[Dict[str, Any]]:
        try:
            import platform as _p
            if _p.system() != "Linux" or _p.machine() != "aarch64":
                return None
            from scripts.detect_hardware import GenericARMLinuxDetector, _is_nvidia_grace
            if _is_nvidia_grace():
                return None   # Grace has its own adapter at higher priority
            return GenericARMLinuxDetector(verbose=False).detect()
        except Exception as exc:
            logger.debug("ARMLinuxAdapter.detect(): %s", exc)
            return None

    def provision(self) -> ProvisionResult:
        result = ProvisionResult(status="success")
        for sub in ("deps", "permissions"):
            step = self._run_provision_script(_PROVISION_SH, sub)
            result.add_step(step.name, step.status, step.message)
            if step.status == "failed":
                result.status = "partial"
        return result

    def verify(self) -> VerificationResult:
        try:
            import json
            hw_config_path = Path("config/hw_config.json")
            hw_config = json.load(open(hw_config_path)) if hw_config_path.exists() else self.detect() or {}
            return self._run_verify_checks(hw_config)
        except Exception as exc:
            result = VerificationResult(passed=False)
            result.add_check("verify", "Hardware verification", False, "critical", str(exc))
            return result
