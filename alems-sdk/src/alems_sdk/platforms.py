"""
alems_sdk.platforms: platform adapter contract (D3.3 hardware detector point).

Physical home since 39.5.1a.2; core.platform.adapter re-exports these objects.
The contract is pure. Running the engine's provision scripts and hardware
checks is runtime behaviour and lives in core (PlatformRuntimeMixin).
Zero imports from core or scripts (INV-14, D2.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from alems_sdk.config_schema import Configurable

@dataclass
class ProvisionStep:
    """
    Result of a single provisioning step.

    Attributes:
        name:    short identifier, e.g. "deps", "permissions", "kernel_module"
        status:  "success" | "partial" | "failed" | "skipped"
        message: human readable detail for logging and install output
    """
    name:    str
    status:  str
    message: str = ""


@dataclass
class ProvisionResult:
    """
    Structured result from platform provisioning.

    Attributes:
        status:          overall outcome, "success" | "partial" | "failed"
        steps:           per step results for install output
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
        name:        check identifier, e.g. "rapl", "spbm", "arm_pmu"
        description: what was checked
        passed:      True if the check passed
        severity:    "critical" | "warning" | "info"
        message:     detail for failed or warned checks
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
        checks: per check results
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


class PlatformAdapterABC(Configurable, ABC):
    """
    Contract for platform adapters: detect, provision, verify one platform.

    Class attributes (override):
        PLATFORM_CLASS: matches platform_class in hw_config, e.g. "nvidia_grace"
        PRIORITY:       detection order, lower first; equal priority allowed
                        for mutually exclusive platforms
    """

    PLATFORM_CLASS: str = "unknown"
    PRIORITY:       int = 999

    @abstractmethod
    def detect(self) -> Optional[Dict[str, Any]]:
        """
        Return a hw_config dict if this platform matches this host, else None.

        Must not raise, must not need venv dependencies, must be deterministic,
        and the dict must contain at least {"platform_class": PLATFORM_CLASS}.
        """

    @abstractmethod
    def provision(self) -> ProvisionResult:
        """Run platform provisioning (deps, permissions, kernel modules)."""

    @abstractmethod
    def verify(self) -> VerificationResult:
        """Run platform health checks; passed only when all critical pass."""


__all__ = [
    "ProvisionStep", "ProvisionResult", "VerificationCheck",
    "VerificationResult", "PlatformAdapterABC",
]
