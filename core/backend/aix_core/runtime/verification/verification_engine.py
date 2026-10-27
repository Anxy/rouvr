"""Compatibility shim for retired verification_engine.py.

Retired in AIX Framework Phase F5 (F5-P4).
Canonical verification framework resides in `backend.aix_core.framework.verification`.
This shim re-exports all value objects and mapping helpers for backward compatibility.
"""

from __future__ import annotations

from backend.aix_core.framework.verification.contract import (
    CheckStatus,
    VERIFICATION_TYPES,
    VERIFIER_VERSION,
    VerificationCheck,
    VerificationOutcome,
)
from backend.aix_core.framework.verification.evidence import (
    aggregate_outcome,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)

__all__ = [
    "CheckStatus",
    "VERIFICATION_TYPES",
    "VERIFIER_VERSION",
    "VerificationCheck",
    "VerificationOutcome",
    "aggregate_outcome",
    "build_checks_from_completion_gates",
    "build_checks_from_read_verification",
]
