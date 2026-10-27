"""AIX objective provenance envelopes (packet P0). Frozen, extra=forbid. No I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.contracts import (
    ApprovalPolicy,
    BudgetV1,
    RequiredEffectKind,
)
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

CONTRACT_VERSION = "v1"

ClaimKind = Literal[
    "OUTCOME",
    "SCOPE_IN",
    "SCOPE_OUT",
    "AMBIGUITY",
    "MISSING_INFO",
    "SITUATION",
    "CAUSE",
]
ScopeMode = Literal["BOUNDED", "OPEN_IMPLEMENTATION"]
Standing = Literal["ASSERTED", "HYPOTHESIZED"]
ClaimStatus = Literal["PROPOSED", "ACTIVE", "SUPERSEDED"]
CiteKind = Literal["INTENT_SPAN", "USER_TURN", "CLAIM", "EVIDENCE"]
NeedResultType = Literal[
    "implementation_locations",
    "file_content",
    "symbol",
    "related_tests",
    "related_routes",
    "existence",
]
NeedSource = Literal["repository", "cycle_workspace"]
CreateCapability = Literal["CREATE_FILE", "CREATE_DIRECTORY"]
TargetKind = Literal["EXISTING", "CREATE"]
PlanRevisionStatus = Literal["DRAFT", "ACCEPTED", "REJECTED", "SUPERSEDED"]
PlanDecision = Literal[
    "ACCEPT",
    "REJECT",
    "NEED_CLARIFICATION",
    "NEED_EVIDENCE",
    "NEED_APPROVAL",
]
VerificationVerdict = Literal["PASS", "REPAIR", "REPLAN", "REUNDERSTAND", "HALT"]


def _uuid() -> str:
    return new_ulid()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = CONTRACT_VERSION


class UserIntent(_Envelope):
    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    definition_of_done: tuple[str, ...] = ()
    dod_user_authored: bool = False
    source_message_id: str | None = None
    conversation_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class UserTurn(_Envelope):
    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_message_id: str | None = None
    created_at: str = Field(default_factory=utc_now)


class ClaimCite(_Envelope):
    kind: CiteKind
    ref: str = Field(min_length=1)


class UnderstandingClaim(_Envelope):
    id: str = Field(default_factory=_uuid)
    kind: ClaimKind
    text: str = Field(min_length=1)
    cites: tuple[ClaimCite, ...] = ()
    standing: Standing = "ASSERTED"
    status: ClaimStatus = "ACTIVE"
    revision: int = Field(ge=1, default=1)


class UnderstandingRevision(_Envelope):
    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    parent_revision_id: str | None = None
    scope_mode: ScopeMode
    claims: tuple[UnderstandingClaim, ...] = ()
    created_at: str = Field(default_factory=utc_now)


class ExplorationNeed(_Envelope):
    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    understanding_revision_id: str = Field(min_length=1)
    claim_ids: tuple[str, ...]
    purpose: str = Field(min_length=1)
    question: str = Field(min_length=1)
    source: NeedSource
    result_type: NeedResultType
    budget_files: int = Field(default=8, ge=1, le=32)


class EvidenceProvenance(_Envelope):
    tool: str = Field(min_length=1)
    query: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)


class ExplorationEvidence(_Envelope):
    id: str = Field(default_factory=_uuid)
    need_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    source: NeedSource
    location: str | None = None
    observation: str = Field(min_length=1)
    exists: bool | None = None
    provenance: EvidenceProvenance
    created_at: str = Field(default_factory=utc_now)


class GovernanceRevision(_Envelope):
    id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    forbidden_effects: tuple[str, ...] = ()
    create_capability: tuple[CreateCapability, ...] = ()
    create_root: tuple[str, ...] = ()
    task_class: str = Field(min_length=1)
    budgets: BudgetV1
    approval_policy: ApprovalPolicy = "INITIAL_PLAN_ONLY"
    created_at: str = Field(default_factory=utc_now)


class PlanTarget(_Envelope):
    id: str = Field(default_factory=_uuid)
    kind: TargetKind
    path: str = Field(min_length=1)
    effect: RequiredEffectKind
    rationale: str = Field(min_length=1)
    evidence_id: str | None = None
    create_capability: CreateCapability | None = None
    create_root: str | None = None
    outcome_claim_ids: tuple[str, ...] = ()


class StepTargetProjection(_Envelope):
    """P0 projection of a step's paths. Not a live StepContractV1."""

    target_scope: tuple[str, ...] = ()


class PlanRevision(_Envelope):
    plan_id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: PlanRevisionStatus = "DRAFT"
    intent_id: str = Field(min_length=1)
    understanding_revision_id: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    attempt_ids: tuple[str, ...] = ()
    governance_revision_id: str = Field(min_length=1)
    approval_ref: str | None = None
    targets: tuple[PlanTarget, ...] = ()
    steps: tuple[StepTargetProjection, ...] = ()
    created_at: str = Field(default_factory=utc_now)


class PlanRequestIntentSection(_Envelope):
    intent_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    turns: tuple[UserTurn, ...] = ()


class PlanRequestClaimView(_Envelope):
    id: str = Field(min_length=1)
    kind: ClaimKind
    text: str = Field(min_length=1)
    standing: Standing
    status: ClaimStatus
    cites: tuple[ClaimCite, ...] = ()


class PlanRequestUnderstandingSection(_Envelope):
    revision_id: str = Field(min_length=1)
    scope_mode: ScopeMode
    claims: tuple[PlanRequestClaimView, ...] = ()


class PlanRequestEvidenceItem(_Envelope):
    id: str = Field(min_length=1)
    need_id: str = Field(min_length=1)
    location: str | None = None
    observation: str = Field(min_length=1)
    exists: bool | None = None


class PlanRequestGovernanceSection(_Envelope):
    revision_id: str = Field(min_length=1)
    forbidden_effects: tuple[str, ...] = ()
    create_capability: tuple[CreateCapability, ...] = ()
    create_root: tuple[str, ...] = ()
    approval_policy: ApprovalPolicy = "INITIAL_PLAN_ONLY"
    create_root_label: Literal["permission"] = "permission"


class PlanRequest(_Envelope):
    """Four typed planner sections. Not a files_read dump and not Plan authority."""

    objective_id: str = Field(min_length=1)
    cycle_id: str = Field(min_length=1)
    intent: PlanRequestIntentSection
    understanding: PlanRequestUnderstandingSection
    evidence: tuple[PlanRequestEvidenceItem, ...] = ()
    governance: PlanRequestGovernanceSection


class AttemptEvidence(_Envelope):
    id: str = Field(default_factory=_uuid)
    plan_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    changed_files: tuple[str, ...] = ()
    verifier_verdict: VerificationVerdict
    reasons: tuple[str, ...] = ()
    created_at: str = Field(default_factory=utc_now)


class ApprovalBinding(_Envelope):
    """Human/system approval attached to exactly one plan_id (A1)."""

    plan_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
