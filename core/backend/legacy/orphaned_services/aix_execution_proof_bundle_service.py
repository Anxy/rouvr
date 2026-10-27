from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from backend.models.execution_proof_bundle_models import ExecutionProofBundleCurrent


@dataclass(frozen=True)
class ExecutionProofBundleValidationResult:
    valid: bool
    bundle: ExecutionProofBundleCurrent | None = None
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def reason_codes(self) -> list[str]:
        return [str(error.get("code") or "").strip() for error in self.errors if str(error.get("code") or "").strip()]


def validate_execution_proof_bundle(
    payload: ExecutionProofBundleCurrent | dict[str, Any] | None,
    *,
    mutation_required: bool = True,
) -> ExecutionProofBundleValidationResult:
    errors: list[dict[str, str]] = []
    if payload is None:
        return ExecutionProofBundleValidationResult(
            valid=False,
            errors=[_error("bundle", "PROOF_BUNDLE_MISSING", "execution proof bundle is required")],
        )

    try:
        bundle = (
            payload
            if isinstance(payload, ExecutionProofBundleCurrent)
            else ExecutionProofBundleCurrent.model_validate(payload)
        )
    except ValidationError as exc:
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc") or []) or "bundle"
            errors.append(_error(location, "PROOF_BUNDLE_SCHEMA_INVALID", str(item.get("msg") or "invalid field")))
        return ExecutionProofBundleValidationResult(valid=False, errors=errors)

    if mutation_required and not bundle.changed_files:
        errors.append(
            _error("changed_files", "PROOF_BUNDLE_CHANGED_FILES_MISSING", "mutation proof requires changed_files")
        )
    if mutation_required and not bundle.git_diff.strip():
        errors.append(_error("git_diff", "PROOF_BUNDLE_DIFF_MISSING", "mutation proof requires git_diff"))
    if mutation_required and bundle.executor_status.strip().upper() == "COMPLETE" and not bundle.git_diff.strip():
        errors.append(_error("git_diff", "COMPLETE_WITHOUT_DIFF", "COMPLETE mutation proof requires git_diff"))

    validation_by_command = {result.command.strip(): result for result in bundle.validation_results}
    for command in bundle.validation_commands:
        normalized = command.strip()
        if normalized and normalized not in validation_by_command:
            errors.append(
                _error(
                    "validation_results",
                    "VALIDATION_RESULT_MISSING",
                    f"validation result missing for command: {normalized}",
                )
            )

    failed_validation = [
        result.command.strip()
        for result in bundle.validation_results
        if result.command.strip() in {command.strip() for command in bundle.validation_commands}
        and int(result.exit_code) != 0
    ]
    if bundle.verifier_decision == "success" and failed_validation:
        errors.append(
            _error(
                "verifier_decision",
                "SUCCESS_WITH_FAILED_VALIDATION",
                "verifier_decision=success requires all validation commands to pass",
            )
        )
    if bundle.verifier_decision == "success" and bundle.executor_status.strip().upper() != "COMPLETE":
        errors.append(
            _error(
                "executor_status",
                "SUCCESS_WITH_NON_COMPLETE_EXECUTOR",
                "verifier_decision=success requires executor_status COMPLETE",
            )
        )

    return ExecutionProofBundleValidationResult(valid=not errors, bundle=bundle, errors=errors)


def _error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}
