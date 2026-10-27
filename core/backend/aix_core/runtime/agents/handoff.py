"""P14: Agent Handoff Contracts (F7 canonical authority when flag on).

Flag OFF → runtime AGENT_SPECS (legacy).
Flag ON → framework Role / SkillMatrix / MultiAgentHandoffProtocol for mapped roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.aix_core.framework.agent.role_map import agent_binding_enabled, to_framework_role
from backend.aix_core.framework.agent.skill_matrix import SkillMatrix
from backend.aix_core.framework.multi_agent.handoff import (
    HandoffAuthorizationError,
    MultiAgentHandoffProtocol,
)
from backend.aix_core.framework.multi_agent.models import (
    MultiAgentHandoffRequest,
    StepContextCapsuleV1,
)
from backend.aix_core.runtime.agents.agent_registry import (
    AgentRole,
    can_delegate,
    get_agent_spec,
)

HandoffStatus = Literal["PENDING", "ACCEPTED", "IN_PROGRESS", "COMPLETED", "FAILED", "REJECTED"]


class HandoffError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HandoffRequestV1:
    """Contract for delegating work between agents."""

    handoff_id: str
    from_agent_id: str
    from_role: AgentRole
    to_role: AgentRole
    cycle_id: str
    step_id: str
    objective: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    budget_tokens: int = 100_000
    budget_wall_clock_seconds: int = 600
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class HandoffResultV1:
    """Structured result returned by the receiving agent."""

    handoff_id: str
    to_agent_id: str
    to_role: AgentRole
    status: HandoffStatus
    outputs: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    error_code: str | None = None
    error_detail: str | None = None
    tokens_used: int = 0
    completed_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


def _validate_via_framework(request: HandoffRequestV1) -> None:
    from_role = to_framework_role(request.from_role)
    to_role = to_framework_role(request.to_role)
    if from_role is None or to_role is None:
        # No framework equivalent — fail closed under flag ON (no silent AGENT_SPECS).
        raise HandoffError(
            "FRAMEWORK_ROLE_UNMAPPED",
            f"roles {request.from_role!r}->{request.to_role!r} lack framework equivalents; "
            "cannot use duplicate AGENT_SPECS under AIX_FRAMEWORK_AGENT_BINDING",
        )
    purpose = str(request.objective or "").strip() or str(
        (request.context or {}).get("purpose") or ""
    ).strip()
    if not purpose:
        raise HandoffError("EMPTY_OBJECTIVE", "handoff must specify an objective")
    files = tuple(
        str(x) for x in (request.context or {}).get("relevant_files", ()) or () if str(x).strip()
    )
    if not files:
        files = ("cycle-workspace",)
    unit_id = str(request.step_id or request.handoff_id or "unit").strip() or "unit"
    work_id = str(request.cycle_id or request.handoff_id or "work").strip() or "work"
    capsule = StepContextCapsuleV1(
        unit_id=unit_id,
        work_id=work_id,
        assigned_role=to_role,
        purpose=purpose,
        target_scope=files,
        acceptance_criteria=tuple(str(x) for x in (request.expected_outputs or ()) if str(x).strip()),
        context_data=dict(request.context or {}),
    )
    fw_req = MultiAgentHandoffRequest(
        from_role=from_role,
        to_role=to_role,
        unit_id=unit_id,
        work_id=work_id,
        context_capsule=capsule,
    )
    try:
        MultiAgentHandoffProtocol.validate_handoff(fw_req)
    except HandoffAuthorizationError as exc:
        raise HandoffError("DELEGATION_NOT_AUTHORIZED", str(exc)) from exc
    except ValueError as exc:
        raise HandoffError("INVALID_HANDOFF", str(exc)) from exc
    # Canonical Role→Skill presence (does not invent a second orchestrator).
    skills = SkillMatrix.skills_for_role(to_role)
    if not skills:
        raise HandoffError("SKILL_MATRIX_EMPTY", f"no skills registered for {to_role.value}")


def _validate_via_registry(request: HandoffRequestV1) -> None:
    if not can_delegate(request.from_role, request.to_role):
        raise HandoffError(
            "DELEGATION_NOT_AUTHORIZED",
            f"{request.from_role} cannot delegate to {request.to_role}",
        )
    target_spec = get_agent_spec(request.to_role)
    if target_spec is None:
        raise HandoffError(
            "UNKNOWN_AGENT_ROLE",
            f"no agent spec registered for role {request.to_role!r}",
        )
    if not request.objective.strip():
        raise HandoffError("EMPTY_OBJECTIVE", "handoff must specify an objective")


def validate_handoff(request: HandoffRequestV1, *, settings: object | None = None) -> None:
    """Authorize handoff via F7 framework path when flag on; else runtime registry."""
    if agent_binding_enabled(settings):
        _validate_via_framework(request)
        return
    _validate_via_registry(request)


def accept_handoff(request: HandoffRequestV1, agent_id: str) -> HandoffResultV1:
    """Create an initial accepted result for tracking."""
    return HandoffResultV1(
        handoff_id=request.handoff_id,
        to_agent_id=agent_id,
        to_role=request.to_role,
        status="ACCEPTED",
    )


def complete_handoff(
    request: HandoffRequestV1,
    agent_id: str,
    *,
    outputs: dict[str, Any],
    evidence_refs: tuple[str, ...] = (),
    tokens_used: int = 0,
) -> HandoffResultV1:
    return HandoffResultV1(
        handoff_id=request.handoff_id,
        to_agent_id=agent_id,
        to_role=request.to_role,
        status="COMPLETED",
        outputs=outputs,
        evidence_refs=evidence_refs,
        tokens_used=tokens_used,
    )


def fail_handoff(
    request: HandoffRequestV1,
    agent_id: str,
    *,
    error_code: str,
    error_detail: str = "",
    tokens_used: int = 0,
) -> HandoffResultV1:
    return HandoffResultV1(
        handoff_id=request.handoff_id,
        to_agent_id=agent_id,
        to_role=request.to_role,
        status="FAILED",
        error_code=error_code,
        error_detail=error_detail,
        tokens_used=tokens_used,
    )


__all__ = [
    "HandoffStatus",
    "HandoffError",
    "HandoffRequestV1",
    "HandoffResultV1",
    "validate_handoff",
    "accept_handoff",
    "complete_handoff",
    "fail_handoff",
]
