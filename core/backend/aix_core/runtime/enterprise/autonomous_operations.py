"""P29: Enterprise Autonomous Operations.

The mature destination: a user can say "Upgrade this service, fix regressions,
update tests, open the release, deploy to staging, monitor it, and only ask me
if production approval is required."

AIX can: Understand, Plan, Research, Authorize, Execute, Test, Repair, Review,
Commit, Deploy, Observe, Rollback, Report, Remember — while every action
remains attributable, bounded, and governed.

This module defines the AutonomousOperationV1 contract and the orchestration
entry point that composes all prior P0–P28 capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

OperationPhase = Literal[
    "understand", "plan", "research", "authorize", "execute",
    "test", "repair", "review", "commit", "deploy",
    "observe", "rollback", "report", "remember", "complete",
]


@dataclass
class AutonomousOperationV1:
    """A complete autonomous operation spanning the full AIX lifecycle."""
    operation_id: str
    tenant_id: str
    project_id: str
    objective: str
    phase: OperationPhase = "understand"
    phases_completed: list[OperationPhase] = field(default_factory=list)
    requires_approval_for: tuple[str, ...] = ("deploy_production", "rollback")
    auto_repair: bool = True
    auto_replan: bool = True
    max_repair_attempts: int = 3
    budget_usd: float = 0.0
    timeout_hours: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    completed_at: str | None = None
    outcome: Literal["pending", "success", "partial", "failed", "cancelled"] = "pending"
    evidence_refs: list[str] = field(default_factory=list)
    audit_refs: list[str] = field(default_factory=list)
    human_interventions: int = 0


@dataclass(frozen=True)
class PhaseTransitionV1:
    from_phase: OperationPhase
    to_phase: OperationPhase
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


PHASE_ORDER: tuple[OperationPhase, ...] = (
    "understand", "plan", "research", "authorize", "execute",
    "test", "repair", "review", "commit", "deploy",
    "observe", "rollback", "report", "remember", "complete",
)

ALLOWED_TRANSITIONS: dict[OperationPhase, tuple[OperationPhase, ...]] = {
    "understand": ("plan",),
    "plan": ("research", "authorize"),
    "research": ("plan", "authorize"),
    "authorize": ("execute",),
    "execute": ("test", "repair"),
    "test": ("repair", "review", "execute"),
    "repair": ("execute", "plan", "report"),
    "review": ("commit", "repair"),
    "commit": ("deploy", "report"),
    "deploy": ("observe", "rollback"),
    "observe": ("report", "rollback", "repair"),
    "rollback": ("report", "plan"),
    "report": ("remember", "complete"),
    "remember": ("complete",),
    "complete": (),
}


class AutonomousOperationOrchestrator:
    """Orchestrates autonomous operations through governed phases."""

    def __init__(self) -> None:
        self._operations: dict[str, AutonomousOperationV1] = {}
        self._transitions: dict[str, list[PhaseTransitionV1]] = {}

    def create(self, op: AutonomousOperationV1) -> AutonomousOperationV1:
        self._operations[op.operation_id] = op
        self._transitions[op.operation_id] = []
        return op

    def transition(
        self, operation_id: str, to_phase: OperationPhase, *, reason: str = "",
    ) -> PhaseTransitionV1 | None:
        op = self._operations.get(operation_id)
        if op is None:
            return None
        allowed = ALLOWED_TRANSITIONS.get(op.phase, ())
        if to_phase not in allowed:
            return None
        transition = PhaseTransitionV1(
            from_phase=op.phase, to_phase=to_phase, reason=reason,
        )
        op.phases_completed.append(op.phase)
        op.phase = to_phase
        self._transitions[operation_id].append(transition)
        if to_phase == "complete":
            op.outcome = "success"
            op.completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return transition

    def get(self, operation_id: str) -> AutonomousOperationV1 | None:
        return self._operations.get(operation_id)

    def get_transitions(self, operation_id: str) -> list[PhaseTransitionV1]:
        return list(self._transitions.get(operation_id, []))

    def requires_approval(self, operation_id: str, action: str) -> bool:
        op = self._operations.get(operation_id)
        if op is None:
            return True  # fail closed
        return action in op.requires_approval_for

    def record_intervention(self, operation_id: str) -> None:
        op = self._operations.get(operation_id)
        if op:
            op.human_interventions += 1

    def cancel(self, operation_id: str) -> bool:
        op = self._operations.get(operation_id)
        if op is None or op.outcome != "pending":
            return False
        op.outcome = "cancelled"
        op.completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return True


__all__ = [
    "OperationPhase", "AutonomousOperationV1", "PhaseTransitionV1",
    "PHASE_ORDER", "ALLOWED_TRANSITIONS", "AutonomousOperationOrchestrator",
]
