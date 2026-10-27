"""EX2-S007: objective_cycle adapter + verification/learning hooks (Packet 7/8).

Contract-facing helpers — not a live objective_cycle runtime. Maps adapter
outcomes through Packet 8 verification before Work Item VERIFIED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from backend.aix_core.runtime.executor.context_builder import ContextCapsule, KnowledgeEntry
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

AdapterOutcomeKind = Literal["SUCCEEDED", "FAILED", "BLOCKED", "TIMED_OUT"]
WorkItemTerminal = Literal["VERIFIED", "FAILED", "BLOCKED", "AWAITING_VERIFICATION"]
DecisionVerdict = Literal["ACCEPT", "REJECT", "WAIVE"]


@dataclass(frozen=True)
class AdapterOutcome:
    kind: AdapterOutcomeKind
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    reason_code: str | None = None
    proposal_hint: str | None = None
    cycle_id: str | None = None


@dataclass(frozen=True)
class DispatchRequest:
    """What the kernel hands the v1 objective_cycle adapter."""

    work_item_id: str
    tenant_id: str
    capsule_content_sha256: str
    max_tokens: int
    timeout_budget_seconds: int
    constraints: list[Any] = field(default_factory=list)
    acceptance: list[Any] = field(default_factory=list)
    model_routes: dict[str, Any] = field(default_factory=dict)


WaiverApproverRole = Literal["planner", "human"]


@dataclass(frozen=True)
class VerificationResult:
    work_item_status: WorkItemTerminal
    decision: DecisionVerdict
    evidence_refs: tuple[str, ...]
    reason_code: str | None = None
    waiver_id: str | None = None
    waiver_rationale: str | None = None
    waiver_approver_actor_id: str | None = None
    waiver_approver_role: WaiverApproverRole | None = None
    waiver_expires_at: str | None = None


def build_dispatch_request(
    *,
    capsule: ContextCapsule,
    timeout_budget_seconds: int = 600,
    model_routes: dict[str, Any] | None = None,
) -> DispatchRequest:
    return DispatchRequest(
        work_item_id=str(capsule.work_item.get("work_item_id") or ""),
        tenant_id=capsule.tenant_id,
        capsule_content_sha256=capsule.capsule_content_sha256,
        max_tokens=capsule.max_tokens,
        timeout_budget_seconds=timeout_budget_seconds,
        constraints=list(capsule.constraints),
        acceptance=list(capsule.acceptance),
        model_routes=dict(model_routes or {}),
    )


def map_adapter_outcome_without_verification(outcome: AdapterOutcome) -> WorkItemTerminal:
    """Adapter alone never grants VERIFIED (Packet 7 §5.4)."""
    if outcome.kind == "SUCCEEDED":
        return "AWAITING_VERIFICATION"
    if outcome.kind == "BLOCKED":
        return "BLOCKED"
    return "FAILED"


def verify_adapter_outcome(
    *,
    outcome: AdapterOutcome,
    decision: DecisionVerdict,
    required_evidence: bool = True,
    waiver_id: str | None = None,
    waiver_rationale: str | None = None,
    waiver_approver_actor_id: str | None = None,
    waiver_approver_role: WaiverApproverRole | None = None,
    waiver_expires_at: str | None = None,
) -> VerificationResult:
    """Packet 8 gate: SUCCEEDED + ACCEPT/WAIVE → VERIFIED; else FAILED/BLOCKED.

    EX3-S004: a WAIVE now requires the structured waiver
    `AIX-EXECUTION-ARCHITECTURE.md` §11.4 specifies — rationale, a named
    approver, and an approver role that is never `executor` (self-waiver
    is explicitly forbidden by that design; the prior implementation only
    checked for a non-empty `waiver_id` string, which enforced none of
    this). `expires_at` is optional here (re-verification-trigger
    enforcement is a separate, not-yet-implemented concern — see this
    task's known gaps) but is threaded through and recorded when supplied,
    so it is not silently dropped once a caller starts providing it.
    """
    if outcome.kind == "BLOCKED":
        return VerificationResult(
            work_item_status="BLOCKED",
            decision=decision,
            evidence_refs=outcome.evidence_refs,
            reason_code=outcome.reason_code or "ADAPTER_BLOCKED",
        )
    if outcome.kind in {"FAILED", "TIMED_OUT"}:
        return VerificationResult(
            work_item_status="FAILED",
            decision=decision,
            evidence_refs=outcome.evidence_refs,
            reason_code=outcome.reason_code or outcome.kind,
        )
    if outcome.kind != "SUCCEEDED":
        raise ExecutionKernelRepositoryError(f"unknown adapter outcome: {outcome.kind}")

    if decision == "REJECT":
        return VerificationResult(
            work_item_status="FAILED",
            decision=decision,
            evidence_refs=outcome.evidence_refs,
            reason_code="VERIFICATION_REJECTED",
        )
    if decision == "WAIVE":
        if not waiver_id:
            raise ExecutionKernelRepositoryError("waiver_id required for WAIVE")
        if not str(waiver_rationale or "").strip():
            raise ExecutionKernelRepositoryError("waiver_rationale is required for WAIVE (§11.4)")
        if not str(waiver_approver_actor_id or "").strip():
            raise ExecutionKernelRepositoryError("waiver_approver_actor_id is required for WAIVE (§11.4)")
        if waiver_approver_role not in ("planner", "human"):
            raise ExecutionKernelRepositoryError(
                "waiver_approver_role must be 'planner' or 'human' — never Executor self-waiver (§11.4)"
            )
        return VerificationResult(
            work_item_status="VERIFIED",
            decision=decision,
            evidence_refs=outcome.evidence_refs,
            reason_code="WAIVED",
            waiver_id=waiver_id,
            waiver_rationale=str(waiver_rationale).strip(),
            waiver_approver_actor_id=str(waiver_approver_actor_id).strip(),
            waiver_approver_role=waiver_approver_role,
            waiver_expires_at=waiver_expires_at,
        )
    if required_evidence and not outcome.evidence_refs:
        return VerificationResult(
            work_item_status="FAILED",
            decision=decision,
            evidence_refs=(),
            reason_code="EVIDENCE_REQUIRED",
        )
    return VerificationResult(
        work_item_status="VERIFIED",
        decision=decision,
        evidence_refs=outcome.evidence_refs,
        reason_code=None,
    )


def distill_knowledge_proposals(
    *,
    tenant_id: str,
    work_item_id: str,
    problem_id: str | None,
    capability_id: str | None,
    evidence_refs: list[str] | tuple[str, ...],
    decision: DecisionVerdict,
    work_item_status: WorkItemTerminal,
    lesson_summary: str | None = None,
    promote_pattern: bool = False,
) -> list[KnowledgeEntry]:
    """Packet 8 Learning Distiller contract — propose Knowledge create payloads.

    No-ops on REJECT with no lesson. Never invents architecture/business kinds
    without an explicit summary path (those need Proposal Level ≥ 2).
    """
    if work_item_status not in {"VERIFIED", "FAILED"}:
        return []
    if decision == "REJECT" and not lesson_summary:
        return []
    summary = (lesson_summary or "").strip()
    if not summary:
        # Minimal deterministic lesson from evidence refs when none provided.
        if not evidence_refs:
            return []
        summary = f"Work item {work_item_id} {work_item_status.lower()} with evidence {', '.join(evidence_refs)}"

    ideas: list[KnowledgeEntry] = [
        KnowledgeEntry(
            knowledge_entry_id=f"kl_{new_ulid()[:12]}",
            tenant_id=tenant_id,
            knowledge_kind="lesson_learned",
            capability_id=capability_id,
            problem_id=problem_id,
            content=summary,
            status="ACTIVE",
            revision=1,
        )
    ]
    if promote_pattern and work_item_status == "VERIFIED" and capability_id:
        ideas.append(
            KnowledgeEntry(
                knowledge_entry_id=f"kp_{new_ulid()[:12]}",
                tenant_id=tenant_id,
                knowledge_kind="pattern",
                capability_id=capability_id,
                problem_id=problem_id,
                content=summary,
                applicable_capability_ids=(capability_id,),
                status="ACTIVE",
                revision=1,
            )
        )
    return ideas


__all__ = [
    "AdapterOutcome",
    "AdapterOutcomeKind",
    "DecisionVerdict",
    "DispatchRequest",
    "VerificationResult",
    "WorkItemTerminal",
    "build_dispatch_request",
    "map_adapter_outcome_without_verification",
    "verify_adapter_outcome",
    "distill_knowledge_proposals",
]
