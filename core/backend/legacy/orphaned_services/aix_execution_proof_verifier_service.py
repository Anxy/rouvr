from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backend.models.execution_proof_bundle_models import ExecutionProofBundleCurrent
from backend.legacy.orphaned_services.aix_execution_proof_bundle_service import (
    ExecutionProofBundleValidationResult,
    validate_execution_proof_bundle,
)

ProofVerifierStatus = Literal["success", "failure", "incomplete"]


@dataclass(frozen=True)
class ExecutionProofVerifierDecision:
    status: ProofVerifierStatus
    reason_code: str
    summary: str
    validation: ExecutionProofBundleValidationResult
    reasons: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "aix.execution_proof_verifier.current",
            "status": self.status,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "proof_bundle_valid": self.validation.valid,
            "proof_bundle_reason_codes": self.validation.reason_codes,
            "proof_bundle_errors": list(self.validation.errors),
        }


def verify_execution_proof_bundle(
    payload: ExecutionProofBundleCurrent | dict[str, Any] | None,
    *,
    mutation_required: bool = True,
) -> ExecutionProofVerifierDecision:
    validation = validate_execution_proof_bundle(payload, mutation_required=mutation_required)
    if not validation.valid:
        return ExecutionProofVerifierDecision(
            status="incomplete",
            reason_code=_first_reason_code(validation, fallback="PROOF_BUNDLE_INVALID"),
            summary="execution proof bundle is incomplete or invalid",
            validation=validation,
            reasons=list(validation.errors),
        )

    bundle = validation.bundle
    if bundle is None:
        return ExecutionProofVerifierDecision(
            status="incomplete",
            reason_code="PROOF_BUNDLE_MISSING",
            summary="execution proof bundle is missing",
            validation=validation,
            reasons=[{"field": "bundle", "code": "PROOF_BUNDLE_MISSING", "message": "proof bundle missing"}],
        )

    failed_validation = [
        result
        for result in bundle.validation_results
        if result.command.strip() in {command.strip() for command in bundle.validation_commands}
        and int(result.exit_code) != 0
    ]
    if failed_validation:
        return ExecutionProofVerifierDecision(
            status="failure",
            reason_code="VALIDATION_FAILED",
            summary="one or more validation commands failed",
            validation=validation,
            reasons=[
                {
                    "field": "validation_results",
                    "code": "VALIDATION_FAILED",
                    "message": f"{result.command.strip()} exited {result.exit_code}",
                }
                for result in failed_validation
            ],
        )

    executor_status = bundle.executor_status.strip().upper()
    if executor_status != "COMPLETE":
        return ExecutionProofVerifierDecision(
            status="failure",
            reason_code="EXECUTOR_NOT_COMPLETE",
            summary="executor did not report COMPLETE",
            validation=validation,
            reasons=[
                {
                    "field": "executor_status",
                    "code": "EXECUTOR_NOT_COMPLETE",
                    "message": f"executor_status={bundle.executor_status}",
                }
            ],
        )

    return ExecutionProofVerifierDecision(
        status="success",
        reason_code="PROOF_VERIFIED",
        summary="execution proof bundle satisfies the verifier evidence contract",
        validation=validation,
        reasons=[],
    )


def _first_reason_code(validation: ExecutionProofBundleValidationResult, *, fallback: str) -> str:
    for code in validation.reason_codes:
        if code:
            return code
    return fallback
