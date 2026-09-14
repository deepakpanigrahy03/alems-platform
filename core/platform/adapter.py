#!/usr/bin/env python3
"""
================================================================================
PLATFORM ADAPTER ABC  —  core/platform/adapter.py
================================================================================

Purpose:
    Defines the contract every platform adapter must satisfy.
    Three responsibilities per adapter:
        detect()    — probe hardware, return hw_config dict or None
        provision() — run platform setup (deps, permissions, kernel modules)
        verify()    — run platform health checks, return structured results

    Two class attributes per adapter:
        PLATFORM_CLASS: str  — matches platform_class in hw_config.json
        PRIORITY:       int  — detection order (lower runs first)

Design:
    detect() is called in PRIORITY order during startup Step 2.
    First adapter whose detect() returns non-None wins.
    Same-priority adapters are allowed when mutually exclusive by hardware.

    provision() and verify() are called by install.sh and verify_hardware.py
    through the platform registry — they never need to know which concrete
    adapter is active.

    detect() must not raise. Must not require venv or pip dependencies
    because it runs before venv exists in install Pass 0.
    detect() must be deterministic for the same host state.

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part A
================================================================================
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProvisionStep:
    """
    Result of a single provisioning step.

    Attributes:
        name:    Short identifier e.g. "deps", "permissions", "kernel_module"
        status:  "success" | "partial" | "failed" | "skipped"
        message: Human-readable detail for logging and install output
    """
    name:    str
    status:  str
    message: str = ""


@dataclass
class ProvisionResult:
    """
    Structured result from platform provisioning.

    Attributes:
        status:          Overall outcome — "success" | "partial" | "failed"
        steps:           Per-step results for install output
        requires_reboot: True if a reboot is needed before verification
    """
    status:          str
    steps:           List[ProvisionStep] = field(default_factory=list)
    requires_reboot: bool = False

    def add_step(self, name: str, status: str, message: str = "") -> None:
        """Append a step result."""
        self.steps.append(ProvisionStep(name=name, status=status, message=message))


@dataclass
class VerificationCheck:
    """
    Result of a single verification check.

    Attributes:
        name:        Check identifier e.g. "rapl", "spbm", "arm_pmu"
        description: Human-readable description of what was checked
        passed:      True if the check passed
        severity:    "critical" | "warning" | "info"
        message:     Detail message for failed or warned checks
    """
    name:        str
    description: str
    passed:      bool
    severity:    str = "critical"
    message:     str = ""


@dataclass
class VerificationResult:
    """
    Structured result from platform verification.

    Attributes:
        passed: True if all critical checks passed
        checks: Per-check results for install output and alems dev status
    """
    passed: bool
    checks: List[VerificationCheck] = field(default_factory=list)

    def add_check(
        self,
        name:        str,
        description: str,
        passed:      bool,
        severity:    str = "critical",
        message:     str = "",
    ) -> None:
        """Append a check result."""
        self.checks.append(VerificationCheck(
            name=name,
            description=description,
            passed=passed,
            severity=severity,
            message=message,
        ))


# ---------------------------------------------------------------------------
# PlatformAdapterABC
# ---------------------------------------------------------------------------

class PlatformAdapterABC(ABC):
    """
    Abstract base class for A-LEMS platform adapters.

    Every platform adapter wraps the existing detector and shell scripts
    for one hardware platform. The adapter is the single Python object
    that represents everything A-LEMS needs to know about a platform:
    how to detect it, how to provision it, and how to verify it.

    Class attributes (must be overridden):
        PLATFORM_CLASS: str — matches platform_class in hw_config.json
                              e.g. "nvidia_grace", "intel_x86", "synthetic"
        PRIORITY:       int — detection order (lower runs first)
                              same-priority is allowed for mutually exclusive
                              platforms (e.g. intel_x86 and amd_x86 both 200)

    Methods (must be implemented):
        detect()    — probe hardware, return hw_config dict or None
        provision() — run platform setup, return ProvisionResult
        verify()    — run health checks, return VerificationResult
    """

    PLATFORM_CLASS: str = "unknown"
    PRIORITY:       int = 999

    @abstractmethod
    def detect(self) -> Optional[Dict[str, Any]]:
        """
        Probe this machine and return hw_config dict if this platform matches.

        Called in PRIORITY order during startup. First non-None result wins.
        All other adapters are skipped.

        Requirements:
            Must not raise — return None on any error.
            Must not require venv or pip deps (runs before venv in Pass 0).
            Must be deterministic for the same host state.
            Must return a dict with at least {"platform_class": PLATFORM_CLASS}
            when this platform is detected.

        Returns:
            Dict hw_config if this platform detected, None otherwise.
        """

    @abstractmethod
    def provision(self) -> ProvisionResult:
        """
        Run platform-specific provisioning (deps, permissions, kernel modules).

        Called by install.sh via the platform registry after venv is active.
        Wraps the existing scripts/platforms/<name>/provision.sh scripts.

        Returns:
            ProvisionResult with per-step outcomes.
        """

    @abstractmethod
    def verify(self) -> VerificationResult:
        """
        Run platform health checks and return structured results.

        Called by install.sh and verify_hardware.py via the platform registry.
        Uses verify_hardware.py's run_checks() as the implementation backend.

        Returns:
            VerificationResult with per-check outcomes.
            passed=True only when all critical checks pass.
        """

    # ------------------------------------------------------------------
    # Shared helpers available to all platform adapters
    # ------------------------------------------------------------------

    def _run_provision_script(
        self,
        script_path: str,
        subcommand: str,
    ) -> ProvisionStep:
        """
        Run a provision.sh subcommand and return a ProvisionStep.

        Args:
            script_path: Absolute path to provision.sh
            subcommand:  "deps" | "permissions" | "models"

        Returns:
            ProvisionStep with outcome of the subprocess call.
        """
        import subprocess
        from pathlib import Path

        if not Path(script_path).exists():
            return ProvisionStep(
                name=subcommand,
                status="skipped",
                message=f"No script at {script_path}",
            )

        try:
            result = subprocess.run(
                ["bash", script_path, subcommand],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode == 0:
                return ProvisionStep(
                    name=subcommand,
                    status="success",
                    message=result.stdout.strip()[-200:] if result.stdout else "",
                )
            else:
                return ProvisionStep(
                    name=subcommand,
                    status="failed",
                    message=result.stderr.strip()[-200:] if result.stderr else "",
                )
        except subprocess.TimeoutExpired:
            return ProvisionStep(
                name=subcommand,
                status="failed",
                message=f"{subcommand} timed out after 300s",
            )
        except Exception as exc:
            return ProvisionStep(
                name=subcommand,
                status="failed",
                message=str(exc),
            )

    def _run_verify_checks(self, hw_config: Dict[str, Any]) -> VerificationResult:
        """
        Run verify_hardware.py's run_checks() and convert to VerificationResult.

        This is the implementation backend for verify() in all real platform
        adapters. Synthetic adapter overrides verify() directly.

        Args:
            hw_config: hw_config dict from detect() or hw_config.json

        Returns:
            VerificationResult populated from run_checks() output.
        """
        try:
            import sys
            from pathlib import Path

            # Add scripts/ to path so verify_hardware imports work
            scripts_dir = str(Path(__file__).parent.parent.parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)

            from verify_hardware import run_checks
            results = run_checks(hw_config)

            optional = {"tsc", "turbostat", "ring_bus"}
            checks = []
            all_passed = True

            for name, passed in results.items():
                severity = "warning" if name in optional else "critical"
                check = VerificationCheck(
                    name=name,
                    description=name.replace("_", " ").title(),
                    passed=bool(passed),
                    severity=severity,
                    message="" if passed else f"{name} check failed",
                )
                checks.append(check)
                if not passed and severity == "critical":
                    all_passed = False

            return VerificationResult(passed=all_passed, checks=checks)

        except Exception as exc:
            logger.error("Platform verification failed: %s", exc)
            return VerificationResult(
                passed=False,
                checks=[VerificationCheck(
                    name="verify_hardware",
                    description="Hardware verification",
                    passed=False,
                    severity="critical",
                    message=str(exc),
                )],
            )
