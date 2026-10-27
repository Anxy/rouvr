"""AIX Verification Framework."""

from backend.aix_core.framework.verification.catalog import (
    GateCatalog,
    GateDefinition,
    ImmutableGateError,
    build_default_catalog,
)
from backend.aix_core.framework.verification.contract import (
    CheckStatus,
    GateContext,
    GateResult,
    VERIFICATION_TYPES,
    VERIFIER_VERSION,
    VerificationCheck,
    VerificationContract,
    VerificationOutcome,
)
from backend.aix_core.framework.verification.evidence import (
    EvidenceBundle,
    EvidenceBundleBuilder,
    aggregate_outcome,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)
from backend.aix_core.framework.verification.pipeline import (
    VerificationPipeline,
)

__all__ = [
    "CheckStatus",
    "EvidenceBundle",
    "EvidenceBundleBuilder",
    "GateCatalog",
    "GateContext",
    "GateDefinition",
    "GateResult",
    "ImmutableGateError",
    "VERIFICATION_TYPES",
    "VERIFIER_VERSION",
    "VerificationCheck",
    "VerificationContract",
    "VerificationOutcome",
    "VerificationPipeline",
    "aggregate_outcome",
    "build_checks_from_completion_gates",
    "build_checks_from_read_verification",
    "build_default_catalog",
]
