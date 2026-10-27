from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERIFIER_DECISION_SCHEMA_VERSION_Current = "verifier.decision.current"
VerifierDecisionCode = Literal["verified", "needs_fix"]
VerifierReasonKind = Literal["bug", "missing_test", "acceptance_gap", "risk", "style"]


class VerifierReasonCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: VerifierReasonKind
    message: str = Field(..., min_length=1)
    file_path: str | None = Field(default=None, min_length=1)
    line: int | None = Field(default=None, ge=1)
    acceptance_id: str | None = None


class VerifierFailedAcceptanceItemCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acceptance_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    file_path: str | None = None
    line: int | None = Field(default=None, ge=1)


class VerifierCapabilityContractCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_tier: str = Field(..., min_length=1)
    allowed_actions: list[str] = Field(default_factory=list, min_length=1, max_length=6)
    forbidden_actions: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    recovery_route: str = Field(..., min_length=1)


class VerifierDecisionCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["verifier.decision.current"]
    decision: VerifierDecisionCode
    reason_code: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    reasons: list[VerifierReasonCurrent] = Field(default_factory=list)
    failed_acceptance_items: list[VerifierFailedAcceptanceItemCurrent] = Field(default_factory=list)
    acceptance_mapping: list[dict] = Field(default_factory=list)
    patch_refs: list[str] = Field(default_factory=list)
    test_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    recovery_kind: str | None = None
    requeueable: bool = False
    capability_contract: VerifierCapabilityContractCurrent
    metadata: dict = Field(default_factory=dict)


def validate_verifier_decision(payload: dict) -> VerifierDecisionCurrent:
    decision = VerifierDecisionCurrent.model_validate(payload)
    allow_unlocated_reasons = bool(decision.metadata.get("allow_unlocated_reasons"))
    if decision.decision == "needs_fix":
        if len(decision.reasons) == 0 and not allow_unlocated_reasons:
            raise ValueError("VERIFIER_DECISION_INVALID needs_fix requires at least one line-referenced reason")
        if decision.reasons and not allow_unlocated_reasons:
            has_line_referenced_reason = any(reason.file_path and reason.line for reason in decision.reasons)
            if not has_line_referenced_reason:
                raise ValueError("VERIFIER_DECISION_INVALID needs_fix requires at least one line-referenced reason")
    if decision.decision == "verified" and decision.failed_acceptance_items:
        raise ValueError("VERIFIER_DECISION_INVALID verified cannot include failed_acceptance_items")
    return decision
