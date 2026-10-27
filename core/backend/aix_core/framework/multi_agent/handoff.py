"""AIX Framework Multi-Agent Handoff Protocol.

Sprint: F10 Work Compiler + Multi-Agent Runtime (F10-P4)
Grounded in: F10-MULTI-AGENT-PROTOCOL.md, SkillMatrix, PersonaRegistry.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.agent.skill_matrix import SkillMatrix
from backend.aix_core.framework.multi_agent.models import (
    ActionObservationV1,
    MultiAgentHandoffRequest,
    MultiAgentHandoffResult,
)


class HandoffAuthorizationError(PermissionError):
    """Raised when handoff between roles is not authorized."""


# Canonical governed role transition matrix
AUTHORIZED_HANDOFFS: dict[Role, set[Role]] = {
    Role.PLANNER: {Role.EXECUTOR_CODER, Role.REVIEW_PACKET_CURATOR},
    Role.EXECUTOR_CODER: {Role.VERIFIER, Role.RECOVERY_PLANNER},
    Role.VERIFIER: {Role.REVIEW_PACKET_CURATOR, Role.RECOVERY_PLANNER},
    Role.RECOVERY_PLANNER: {Role.PLANNER, Role.EXECUTOR_CODER},
    Role.REVIEW_PACKET_CURATOR: set(),
}


class MultiAgentHandoffProtocol:
    """Validates and executes governed role handoffs."""

    @classmethod
    def can_handoff(cls, from_role: Role, to_role: Role) -> bool:
        allowed_targets = AUTHORIZED_HANDOFFS.get(from_role, set())
        return to_role in allowed_targets

    @classmethod
    def validate_handoff(cls, request: MultiAgentHandoffRequest) -> None:
        if not cls.can_handoff(request.from_role, request.to_role):
            raise HandoffAuthorizationError(
                f"Unauthorized handoff transition: {request.from_role.value} -> {request.to_role.value}"
            )
        if not request.context_capsule.purpose.strip():
            raise ValueError("Handoff context capsule must specify a purpose")

    @classmethod
    def process_handoff(
        cls,
        request: MultiAgentHandoffRequest,
        observation: ActionObservationV1 | None = None,
    ) -> MultiAgentHandoffResult:
        cls.validate_handoff(request)
        return MultiAgentHandoffResult(
            handoff_id=request.handoff_id,
            from_role=request.from_role,
            to_role=request.to_role,
            status="COMPLETED" if observation else "ACCEPTED",
            observation=observation,
            detail=f"Handoff from {request.from_role.value} to {request.to_role.value} verified",
        )
