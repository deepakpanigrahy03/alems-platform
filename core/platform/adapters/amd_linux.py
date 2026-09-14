#!/usr/bin/env python3
"""AMD Linux Platform Adapter  —  core/platform/adapters/amd_linux.py"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)
_PROVISION_SH = str(Path(__file__).parent.parent.parent.parent / "scripts" / "platforms" / "amd_x86" / "provision.sh")


class AMDLinuxAdapter(PlatformAdapterABC):
    """Platform adapter for AMD Linux x86_64."""

    PLATFORM_CLASS: str = "amd_x86"
    PRIORITY:       int = 200

    def detect(self) -> Optional[Dict[str, Any]]:
        try:
            import platform as _p
            if _p.system() != "Linux" or _p.machine() != "x86_64":
                return None
            from scripts.detect_hardware import AMDLinuxDetector, _detect_x86_vendor
            if _detect_x86_vendor() != "amd":
                return None
            return AMDLinuxDetector(verbose=False).detect()
        except Exception as exc:
            logger.debug("AMDLinuxAdapter.detect(): %s", exc)
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
