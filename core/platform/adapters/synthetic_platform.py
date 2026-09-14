#!/usr/bin/env python3
"""
Synthetic Platform Adapter  —  core/platform/adapters/synthetic_platform.py

Active ONLY when ALEMS_PLATFORM_OVERRIDE=synthetic is set.
Returns a fixed hw_config dict. Never selected on real hardware.
Acts as last-resort platform adapter (PRIORITY=950) so real detectors run first.
"""

import logging
import os
import platform
from typing import Any, Dict, Optional

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)


class SyntheticPlatformAdapter(PlatformAdapterABC):
    """
    Platform adapter for the synthetic test environment.

    detect() returns non-None ONLY when ALEMS_PLATFORM_OVERRIDE=synthetic.
    PRIORITY=950 ensures all real platform adapters are tried first.
    provision() is a no-op (nothing to install on synthetic platform).
    verify() returns passed=True with info-level checks (nothing to verify).
    """

    PLATFORM_CLASS: str = "synthetic"
    PRIORITY:       int = 950

    def detect(self) -> Optional[Dict[str, Any]]:
        """
        Return synthetic hw_config only when override env var is set.

        Returns:
            hw_config dict with platform_class="synthetic" if
            ALEMS_PLATFORM_OVERRIDE=synthetic, None otherwise.
        """
        override = os.environ.get("ALEMS_PLATFORM_OVERRIDE", "").strip().lower()
        if override != "synthetic":
            return None

        logger.info("SyntheticPlatformAdapter: ALEMS_PLATFORM_OVERRIDE=synthetic active")
        return {
            "platform_class":  "synthetic",
            "cpu_vendor":      "synthetic",
            "cpu_model":       "Synthetic CPU",
            "cpu_cores":       4,
            "cpu_threads":     8,
            "os_name":         platform.system(),
            "cpu_architecture": platform.machine(),
            "measurement_mode": "MEASURED",
            "metadata": {
                "hostname":     platform.node(),
                "machine":      platform.machine(),
                "detected_at":  "synthetic",
            },
        }

    def provision(self) -> ProvisionResult:
        """No-op — synthetic platform needs no provisioning."""
        result = ProvisionResult(status="success")
        result.add_step("synthetic", "success", "No provisioning needed on synthetic platform")
        return result

    def verify(self) -> VerificationResult:
        """Always passes — synthetic platform has no hardware to verify."""
        result = VerificationResult(passed=True)
        result.add_check(
            name="synthetic",
            description="Synthetic platform active",
            passed=True,
            severity="info",
            message="ALEMS_PLATFORM_OVERRIDE=synthetic — no hardware verification needed",
        )
        return result
