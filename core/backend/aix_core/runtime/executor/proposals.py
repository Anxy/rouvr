"""EX2-S012: Proposal raise/classify/resolve hooks (Packet 4 lifecycle)."""

from __future__ import annotations

from typing import Any, Literal, Protocol
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

ProposalLevel = Literal[1, 2, 3, 4]
OPEN_PROPOSAL_STATUSES = frozenset({"RAISED", "CLASSIFIED", "ROUTED", "ESCALATED"})
PROPOSAL_LEVELS = frozenset({1, 2, 3, 4})


class ProposalService(Protocol):
    async def append_and_project(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> Any: ...

    async def get_projection(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None: ...


def _default_approver(level: int) -> str:
    if level <= 1:
        return "executor"
    if level <= 3:
        return "planner"
    return "human"


def required_level_for_impact(
    *,
    problem_capability_id: str | None,
    affected_capabilities: list[str] | None,
    requests_new_capability: bool = False,
    charter_change: bool = False,
) -> int:
    """EX3-S001: rule-based Level floor from impact analysis, per
    `AIX-PROPOSAL-SYSTEM.md` §2. This is the guard the Level 3 boundary
    needed: a caller-suggested level is a floor, never a ceiling — the
    actual impact analysis can only escalate it, never lower it below what
    the data implies. Returns the minimum defensible Proposal Level
    (1-4); the caller's own suggested level is raised to this floor, never
    lowered by it.

    Rule-based only (no model-assisted classification here) — that split
    is deliberate, per `AIX-EXECUTION-ONTOLOGY-DECISION.md` §5.
    """
    # Level 4: a new/rescoped Capability is exactly what only a Human may grant.
    if requests_new_capability or charter_change:
        return 4
    affected = [c for c in (affected_capabilities or []) if c]
    own = {problem_capability_id} if problem_capability_id else set()
    other_capabilities = set(affected) - own
    # Level 3: touches an existing Capability other than the Problem's own,
    # but does not itself request a new Capability or charter change.
    if other_capabilities:
        return 3
    # Level 1/2 are both "within this Problem's own Capability" — the
    # caller's own choice between them (local execution detail vs. a plan
    # adjustment) is not inferable from impact analysis alone, so it is not
    # escalated here; that distinction stays the caller's to make.
    return 1


async def raise_proposal(
    service: ProposalService,
    *,
    tenant_id: str,
    problem_id: str,
    reason: str,
    command_id: str,
    level: ProposalLevel = 2,
    work_item_id: str | None = None,
    affected_capabilities: list[str] | None = None,
    estimated_work_item_delta: int = 0,
    risk: Literal["low", "medium", "high"] = "low",
    proposal_id: str | None = None,
    requests_new_capability: bool = False,
    charter_change: bool = False,
) -> dict[str, Any]:
    if int(level) not in PROPOSAL_LEVELS:
        raise ExecutionKernelRepositoryError(f"invalid proposal level={level!r}")
    if not str(reason or "").strip():
        raise ExecutionKernelRepositoryError("proposal reason is required")
    problem = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id
    )
    if problem is None:
        raise ExecutionKernelRepositoryError(f"problem not found: {problem_id}")

    pid = proposal_id or f"prop_{new_ulid()[:12]}"
    if await service.get_projection(tenant_id=tenant_id, aggregate_type="proposal", aggregate_id=pid):
        raise ExecutionKernelRepositoryError(f"proposal already exists: {pid}")

    # EX3-S001: the caller's level is a floor, not a ceiling — the actual
    # impact analysis can only escalate it (never silently lower it), and
    # any escalation is recorded for audit rather than applied silently.
    problem_capability_id = (problem.get("payload") or {}).get("parent_id")
    required_level = required_level_for_impact(
        problem_capability_id=problem_capability_id,
        affected_capabilities=affected_capabilities,
        requests_new_capability=requests_new_capability,
        charter_change=charter_change,
    )
    requested_level_i = int(level)
    level_i = max(requested_level_i, required_level)
    auto_escalated_from = requested_level_i if level_i != requested_level_i else None

    status = "RAISED"
    payload: dict[str, Any] = {
        "status": status,
        "parent_id": problem_id,
        "problem_id": problem_id,
        "work_item_id": work_item_id,
        "level": level_i,
        "reason": str(reason).strip(),
        "approver": _default_approver(level_i),
        "affected_capabilities": list(affected_capabilities or []),
        "estimated_work_item_delta": int(estimated_work_item_delta),
        "risk": risk,
        "requests_new_capability": bool(requests_new_capability),
        "charter_change": bool(charter_change),
        "auto_escalated_from": auto_escalated_from,
    }
    event = await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="proposal",
        aggregate_id=pid,
        expected_sequence_number=1,
        event_type="PROPOSAL_RAISED",
        command_id=command_id,
        payload=payload,
    )

    # Level 1 auto-classifies + resolves (Packet 4 / ADR-007 audit trail).
    if level_i == 1:
        return await resolve_proposal(
            service,
            tenant_id=tenant_id,
            proposal_id=pid,
            command_id=f"{command_id}:l1_resolve",
            decision="RESOLVED",
            classify_first=True,
        )

    # Levels 2–4: raise then classify (routing deferred to human/planner APIs).
    classified = await classify_proposal(
        service,
        tenant_id=tenant_id,
        proposal_id=pid,
        command_id=f"{command_id}:classify",
        level=level_i,  # type: ignore[arg-type]
    )
    return {
        "proposal_id": pid,
        "status": classified["status"],
        "level": level_i,
        "event": {"id": event.id, "sequence_number": event.sequence_number},
        "classified": classified,
    }


async def classify_proposal(
    service: ProposalService,
    *,
    tenant_id: str,
    proposal_id: str,
    command_id: str,
    level: ProposalLevel | None = None,
) -> dict[str, Any]:
    row = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="proposal", aggregate_id=proposal_id
    )
    if row is None:
        raise ExecutionKernelRepositoryError(f"proposal not found: {proposal_id}")
    if row["status"] not in {"RAISED", "CLASSIFIED"}:
        raise ExecutionKernelRepositoryError(f"cannot classify proposal in status={row['status']!r}")
    payload = dict(row.get("payload") or {})
    if level is not None:
        payload["level"] = int(level)
        payload["approver"] = _default_approver(int(level))
    payload["status"] = "CLASSIFIED"
    # Level 2/3 → ROUTED to planner; Level 4 → ROUTED (human). Keep CLASSIFIED then ROUTED.
    event = await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="proposal",
        aggregate_id=proposal_id,
        expected_sequence_number=int(row["sequence_number"]) + 1,
        event_type="PROPOSAL_CLASSIFIED",
        command_id=command_id,
        payload=payload,
    )
    level_i = int(payload.get("level") or 2)
    if level_i >= 2:
        payload["status"] = "ROUTED"
        event = await service.append_and_project(
            tenant_id=tenant_id,
            aggregate_type="proposal",
            aggregate_id=proposal_id,
            expected_sequence_number=int(event.sequence_number) + 1,
            event_type="PROPOSAL_ROUTED",
            command_id=f"{command_id}:routed",
            payload=payload,
        )
    return {
        "proposal_id": proposal_id,
        "status": payload["status"],
        "level": level_i,
        "sequence_number": event.sequence_number,
    }


async def resolve_proposal(
    service: ProposalService,
    *,
    tenant_id: str,
    proposal_id: str,
    command_id: str,
    decision: Literal["RESOLVED", "DENIED", "WITHDRAWN"] = "RESOLVED",
    classify_first: bool = False,
) -> dict[str, Any]:
    row = await service.get_projection(
        tenant_id=tenant_id, aggregate_type="proposal", aggregate_id=proposal_id
    )
    if row is None:
        raise ExecutionKernelRepositoryError(f"proposal not found: {proposal_id}")
    if classify_first and row["status"] == "RAISED":
        await classify_proposal(
            service, tenant_id=tenant_id, proposal_id=proposal_id, command_id=f"{command_id}:classify"
        )
        row = await service.get_projection(
            tenant_id=tenant_id, aggregate_type="proposal", aggregate_id=proposal_id
        )
        assert row is not None
    if row["status"] not in OPEN_PROPOSAL_STATUSES | {"CLASSIFIED"}:
        raise ExecutionKernelRepositoryError(f"cannot resolve proposal in status={row['status']!r}")
    payload = dict(row.get("payload") or {})
    payload["status"] = decision
    event = await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="proposal",
        aggregate_id=proposal_id,
        expected_sequence_number=int(row["sequence_number"]) + 1,
        event_type=f"PROPOSAL_{decision}",
        command_id=command_id,
        payload=payload,
    )
    return {
        "proposal_id": proposal_id,
        "status": decision,
        "level": payload.get("level"),
        "sequence_number": event.sequence_number,
        "event_id": event.id,
    }


def open_proposal_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "") not in OPEN_PROPOSAL_STATUSES:
            continue
        payload = dict(row.get("payload") or {})
        out.append(
            {
                "proposal_id": row.get("aggregate_id"),
                "status": row.get("status"),
                "level": payload.get("level"),
                "reason": payload.get("reason"),
                "approver": payload.get("approver"),
                "work_item_id": payload.get("work_item_id"),
                "risk": payload.get("risk"),
            }
        )
    return out


__all__ = [
    "OPEN_PROPOSAL_STATUSES",
    "ProposalLevel",
    "required_level_for_impact",
    "raise_proposal",
    "classify_proposal",
    "resolve_proposal",
    "open_proposal_dicts",
]
