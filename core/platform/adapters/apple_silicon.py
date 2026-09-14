#!/usr/bin/env python3
"""Apple Silicon Platform Adapter  —  core/platform/adapters/apple_silicon.py"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)
_PROVISION_SH = str(Path(__file__).parent.parent.parent.parent / "scripts" / "platforms" / "apple_silicon" / "provision.sh")


class AppleSiliconAdapter(PlatformAdapterABC):
    """Platform adapter for Apple Silicon macOS (M1/M2/M3)."""

    PLATFORM_CLASS: str = "apple_silicon"
    PRIORITY:       int = 100

    def detect(self) -> Optional[Dict[str, Any]]:
        try:
            import platform as _p
            if _p.system() != "Darwin" or _p.machine() not in ("arm64", "aarch64"):
                return None
            from scripts.detect_hardware import AppleSiliconDetector
            return AppleSiliconDetector(verbose=False).detect()
        except Exception as exc:
            logger.debug("AppleSiliconAdapter.detect(): %s", exc)
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
