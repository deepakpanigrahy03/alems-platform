# core/recovery/__init__.py
# Recovery policy adapter family.
# Chunk 35 INV-2: adapters provide capability to core, never write core tables.

from core.recovery.recovery_adapter import (
    RecoveryDecision,
    RecoveryPolicyAdapter,
    FullRestartPolicy,
    LocalizedRecoveryPolicy,
    RecoveryPolicyRegistry,
)

__all__ = [
    "RecoveryDecision",
    "RecoveryPolicyAdapter",
    "FullRestartPolicy",
    "LocalizedRecoveryPolicy",
    "RecoveryPolicyRegistry",
]
