"""AIX Verification subsystem."""

from backend.aix_core.runtime.verification.objective_truth import (
    CanonicalObjectiveTruthV1,
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    DispositionCoverageResult,
    ObjectiveVerificationContractV1,
    OVCategory,
    OVDispositionKind,
    OVDispositionV1,
    OVItemV1,
    OVProofMethod,
    derive_ovc,
    evaluate_ovc_disposition_coverage,
)
from backend.aix_core.runtime.verification.verification_result import (
    OVItemResultV1,
    ProofStatus,
    VerificationResultV1,
    VerificationScope,
    VerificationStatus,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionKind,
    VerificationDecisionV1,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    GuardsEvaluated,
    TerminalizationDecisionKind,
    TerminalizationDecisionV1,
    evaluate_terminalization,
)
from backend.aix_core.runtime.verification.runtime_probe_runner import (
    ProbeResult,
    RuntimeProbeRunner,
)
from backend.aix_core.runtime.verification.objective_verifier import (
    ObjectiveVerifier,
)
from backend.aix_core.runtime.verification.regression_checker import (
    RegressionChecker,
)
from backend.aix_core.runtime.verification.gap import (
    GapCategory,
    GapSeverity,
    GapStatus,
    GapStore,
    GapV1,
)

__all__ = [
    "CanonicalObjectiveTruthV1",
    "derive_canonical_truth",
    "ObjectiveVerificationContractV1",
    "OVCategory",
    "OVProofMethod",
    "OVDispositionKind",
    "OVDispositionV1",
    "OVItemV1",
    "DispositionCoverageResult",
    "derive_ovc",
    "evaluate_ovc_disposition_coverage",
    "OVItemResultV1",
    "ProofStatus",
    "VerificationResultV1",
    "VerificationScope",
    "VerificationStatus",
    "VerificationDecisionKind",
    "VerificationDecisionV1",
    "GuardsEvaluated",
    "TerminalizationDecisionKind",
    "TerminalizationDecisionV1",
    "evaluate_terminalization",
    "ProbeResult",
    "RuntimeProbeRunner",
    "ObjectiveVerifier",
    "RegressionChecker",
    "GapCategory",
    "GapSeverity",
    "GapStatus",
    "GapStore",
    "GapV1",
]
