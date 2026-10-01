"""
core/platform/adapter.py: platform contract compatibility path plus runtime helpers.

Since 39.5.1a.2 the contract (PlatformAdapterABC and its result dataclasses)
lives in alems_sdk.platforms and is re-exported here as the identical objects
(D2.2, Rule S). The helpers that run the engine's provision scripts and
scripts/verify_hardware.py are runtime behaviour, so they stay here in
PlatformRuntimeMixin; core platform adapters inherit it explicitly.
"""

import logging
from typing import Any, Dict

from alems_sdk.platforms import (  # noqa: F401  (re-export)
    PlatformAdapterABC,
    ProvisionResult,
    ProvisionStep,
    VerificationCheck,
    VerificationResult,
)

logger = logging.getLogger(__name__)


class PlatformRuntimeMixin:
    """Runtime helpers for core platform adapters (moved verbatim from the ABC)."""

    def _run_provision_script(
        self,
        script_path: str,
        subcommand: str,
    ) -> ProvisionStep:
        """
        Run a provision.sh subcommand and return a ProvisionStep.

        Args:
            script_path: absolute path to provision.sh
            subcommand:  "deps" | "permissions" | "models"

        Returns:
            ProvisionStep with the outcome of the subprocess call.
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
        Run verify_hardware.run_checks() and convert to a VerificationResult.

        Backend for verify() in real platform adapters; the synthetic adapter
        overrides verify() directly.
        """
        try:
            import sys
            from pathlib import Path

            # scripts/ on the path so verify_hardware imports resolve
            # (engine root is two levels above core/platform/).
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


__all__ = [
    "PlatformAdapterABC", "PlatformRuntimeMixin", "ProvisionResult",
    "ProvisionStep", "VerificationCheck", "VerificationResult",
]
