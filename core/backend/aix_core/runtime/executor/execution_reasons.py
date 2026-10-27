"""Stable reason ownership for real-execution outcome containment."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExecutionReason:
    reason_code: str
    reason_stage: str
    reason_owner: str
    retryable: bool
    user_action_required: bool
    system_action_required: bool

    def as_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _reason(code: str, stage: str, owner: str, *, retryable: bool = False,
            user: bool = False, system: bool = False) -> ExecutionReason:
    return ExecutionReason(code, stage, owner, retryable, user, system)


REASON_CATALOG: dict[str, ExecutionReason] = {
    "INTENT_UNCLEAR": _reason("INTENT_UNCLEAR", "classification", "aix-objective", user=True),
    "NO_EXECUTABLE_STEPS": _reason("NO_EXECUTABLE_STEPS", "planning", "aix-planner", system=True),
    "NO_REAL_OPERATION_PERFORMED": _reason(
        "NO_REAL_OPERATION_PERFORMED", "execution", "aix-executor", retryable=True, system=True
    ),
    "NO_TARGET_RESOURCE_ACCESSED": _reason(
        "NO_TARGET_RESOURCE_ACCESSED", "execution", "aix-executor", retryable=True, system=True
    ),
    "VERIFICATION_STEP_MISSING": _reason(
        "VERIFICATION_STEP_MISSING", "verification", "aix-verifier", system=True
    ),
    "VERIFICATION_FAILED": _reason(
        "VERIFICATION_FAILED", "verification", "aix-verifier", retryable=True, system=True
    ),
    "CAPABILITY_NOT_REGISTERED": _reason(
        "CAPABILITY_NOT_REGISTERED", "planning", "hyeve-capability-registry", user=True, system=True
    ),
    "EXECUTION_LOOP_DETECTED": _reason(
        "EXECUTION_LOOP_DETECTED", "execution", "aix-cycle", user=True, system=True
    ),
    "WORKSPACE_NOT_BOUND": _reason(
        "WORKSPACE_NOT_BOUND", "resource_resolution", "hyeve-workspace-registry", user=True
    ),
    "WRONG_SOURCE_MESSAGE": _reason(
        "WRONG_SOURCE_MESSAGE", "admission", "aix-objective", user=True, system=True
    ),
    "NO_FILES_CHANGED": _reason("NO_FILES_CHANGED", "execution", "aix-executor", user=True),
    "PATCH_CONTEXT_MISMATCH": _reason("PATCH_CONTEXT_MISMATCH", "execution", "aix-executor", user=True),
    "NO_OP_PATCH": _reason("NO_OP_PATCH", "execution", "aix-executor", user=True),
    "MODEL_RETURNED_NO_EDIT": _reason(
        "MODEL_RETURNED_NO_EDIT", "codegen", "aix-codegen", retryable=True, system=True
    ),
    "EDIT_PARSE_FAILED": _reason(
        "EDIT_PARSE_FAILED", "codegen", "aix-codegen", retryable=True, system=True
    ),
    "EDIT_UNCHANGED": _reason(
        "EDIT_UNCHANGED", "validation", "aix-executor", retryable=True, system=True
    ),
    "EDIT_PATH_INVALID": _reason("EDIT_PATH_INVALID", "validation", "aix-executor", system=True),
    "EDIT_PATH_OUTSIDE_SCOPE": _reason(
        "EDIT_PATH_OUTSIDE_SCOPE", "authorization", "aix-executor", user=True, system=True
    ),
    "EDIT_SCHEMA_INVALID": _reason(
        "EDIT_SCHEMA_INVALID", "codegen", "aix-codegen", retryable=True, system=True
    ),
    "EDIT_VALIDATION_FAILED": _reason(
        "EDIT_VALIDATION_FAILED", "validation", "aix-verifier", retryable=True, system=True
    ),
    "CONTEXT_TOO_SMALL": _reason(
        "CONTEXT_TOO_SMALL", "codegen", "aix-codegen", retryable=True, system=True
    ),
    "CAPABILITY_BLOCKED": _reason(
        "CAPABILITY_BLOCKED", "planning", "aix-executor", user=True, system=True
    ),
    "TARGET_NOT_FOUND": _reason(
        "TARGET_NOT_FOUND", "resource_resolution", "aix-codeintel", user=True
    ),
    "MODEL_REFUSAL": _reason(
        "MODEL_REFUSAL", "codegen", "aix-codegen", retryable=True, system=True
    ),
    "STALE_FILE_VERSION": _reason("STALE_FILE_VERSION", "execution", "aix-executor", retryable=True, user=True),
    "PATCH_VALIDATION_FAILED": _reason("PATCH_VALIDATION_FAILED", "execution", "aix-executor", system=True),
    "PATCH_FAILED": _reason("PATCH_FAILED", "execution", "aix-executor", system=True),
    "SYMBOL_NOT_FOUND": _reason("SYMBOL_NOT_FOUND", "resource_resolution", "aix-codeintel", user=True),
    "SYMBOL_AMBIGUOUS": _reason("SYMBOL_AMBIGUOUS", "resource_resolution", "aix-codeintel", user=True),
    "CREATE_ONLY_VIOLATION": _reason("CREATE_ONLY_VIOLATION", "execution", "aix-executor", user=True),
    "EXPLORATION_BUDGET_EXCEEDED": _reason("EXPLORATION_BUDGET_EXCEEDED", "execution", "aix-budget", user=True, system=True),
    "REPEATED_IDENTICAL_SEARCH": _reason("REPEATED_IDENTICAL_SEARCH", "execution", "aix-budget", user=True, system=True),
    "REPEATED_IDENTICAL_READ": _reason("REPEATED_IDENTICAL_READ", "execution", "aix-budget", user=True, system=True),
    "REPEATED_CLARIFICATION": _reason("REPEATED_CLARIFICATION", "execution", "aix-budget", user=True, system=True),
    "PLAN_REVISION_BUDGET_EXCEEDED": _reason("PLAN_REVISION_BUDGET_EXCEEDED", "execution", "aix-budget", user=True, system=True),
    "CODEGEN_FAILED": _reason("CODEGEN_FAILED", "codegen", "aix-codegen", system=True),
    "COMPLETION_GATES_FAILED": _reason("COMPLETION_GATES_FAILED", "verification", "aix-verifier", retryable=True, system=True),
    "OBJECTIVE_COVERAGE_FAILED": _reason("OBJECTIVE_COVERAGE_FAILED", "verification", "aix-verifier", retryable=True, system=True),
    "VERIFICATION_REPAIR_REQUIRED": _reason("VERIFICATION_REPAIR_REQUIRED", "verification", "aix-verifier", retryable=True, system=True),
    "VERIFICATION_HALT": _reason("VERIFICATION_HALT", "verification", "aix-verifier"),
}


def reason_details(reason_code: str | None) -> dict[str, str | bool] | None:
    if reason_code is None:
        return None
    reason = REASON_CATALOG.get(reason_code)
    if reason is not None:
        return reason.as_dict()
    return ExecutionReason(
        reason_code=reason_code,
        reason_stage="execution",
        reason_owner="aix-objective-cycle",
        retryable=False,
        user_action_required=False,
        system_action_required=True,
    ).as_dict()


__all__ = ["ExecutionReason", "REASON_CATALOG", "reason_details"]
