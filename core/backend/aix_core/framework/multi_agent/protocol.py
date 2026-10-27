"""F10-P4 Multi-Agent protocol — governance over OC, not a second workflow engine.

Agents never become CANONICAL_COMMAND writers. Execution stays F4; verification F5;
recovery F6; WorkHub F8; LLM F9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.agent.role_map import to_framework_role
from backend.aix_core.framework.compiler.stages.assign import AssignedWorkGraph, UnitAssignment
from backend.aix_core.framework.multi_agent.conflict import ConflictDetector
from backend.aix_core.framework.multi_agent.delegation import DelegationManager
from backend.aix_core.framework.multi_agent.handoff import (
    HandoffAuthorizationError,
    MultiAgentHandoffProtocol,
)
from backend.aix_core.framework.multi_agent.models import (
    ActionObservationV1,
    MultiAgentHandoffRequest,
    StepContextCapsuleV1,
)
from backend.aix_core.framework.multi_agent.sync import ScopeLockManager
from backend.aix_core.framework.work.models import WorkGraph, WorkUnit


@dataclass
class MultiAgentSession:
    """In-memory multi-agent session state for one objective compile."""

    work_id: str
    assigned: AssignedWorkGraph
    capsules: dict[str, StepContextCapsuleV1] = field(default_factory=dict)
    observations: dict[str, ActionObservationV1] = field(default_factory=dict)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    collisions: tuple[Any, ...] = ()
    lock_manager: ScopeLockManager = field(default_factory=ScopeLockManager)


class MultiAgentProtocol:
    """Canonical delegation/handoff/context/evidence governance helpers."""

    def __init__(self, lock_manager: ScopeLockManager | None = None) -> None:
        self._locks = lock_manager or ScopeLockManager()
        self._delegation = DelegationManager(lock_manager=self._locks)

    def open_session(self, assigned: AssignedWorkGraph) -> MultiAgentSession:
        collisions = ConflictDetector.detect_graph_collisions(assigned.graph)
        return MultiAgentSession(
            work_id=assigned.graph.work_id,
            assigned=assigned,
            collisions=tuple(collisions),
            lock_manager=self._locks,
        )

    def prepare_unit_capsule(
        self,
        session: MultiAgentSession,
        unit: WorkUnit,
        *,
        context_data: dict[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> StepContextCapsuleV1:
        assignment = session.assigned.assignments.get(unit.unit_id)
        role = Role.EXECUTOR_CODER
        if assignment is not None:
            role = to_framework_role(assignment.assigned_role) or Role.EXECUTOR_CODER
        elif unit.required_agent_role:
            role = to_framework_role(unit.required_agent_role) or Role.EXECUTOR_CODER
        data = dict(context_data or {})
        if evidence_refs:
            data["evidence_refs"] = list(evidence_refs)
        if unit.context_refs:
            data["context_refs"] = list(unit.context_refs)
        capsule = self._delegation.prepare_capsule(unit, role, context_data=data)
        # Enrich required capabilities from assignment
        if assignment is not None and assignment.resolved_capabilities:
            capsule = capsule.model_copy(
                update={
                    "required_capabilities": assignment.resolved_capabilities,
                    "capsule_hash": "",
                }
            )
            capsule = capsule.model_copy(update={"capsule_hash": capsule.compute_hash()})
        session.capsules[unit.unit_id] = capsule
        return capsule

    def authorize_handoff(
        self,
        session: MultiAgentSession,
        *,
        from_role: str,
        to_role: str,
        unit: WorkUnit,
        settings: object | None = None,
    ) -> None:
        """Validate handoff via F7 framework path (MULTI_AGENT implies AGENT_BINDING semantics)."""
        # Lazy import avoids circular import with runtime.agents.handoff ↔ multi_agent.
        from backend.aix_core.runtime.agents.handoff import HandoffRequestV1, validate_handoff

        req = HandoffRequestV1(
            handoff_id=f"ho_{unit.unit_id}_{from_role}_{to_role}",
            from_agent_id=f"agent_{from_role}",
            from_role=from_role,  # type: ignore[arg-type]
            to_role=to_role,  # type: ignore[arg-type]
            cycle_id=session.work_id,
            step_id=unit.unit_id,
            objective=unit.purpose,
            context={
                "relevant_files": list(unit.target_scope),
                "purpose": unit.purpose,
                "evidence_refs": list(unit.evidence_refs),
                "context_refs": list(unit.context_refs),
            },
        )
        # Force framework path for multi-agent sessions
        class _S:
            AIX_FRAMEWORK_AGENT_BINDING = True

        validate_handoff(req, settings=settings or _S())
        session.handoffs.append(
            {
                "from_role": from_role,
                "to_role": to_role,
                "unit_id": unit.unit_id,
                "status": "AUTHORIZED",
            }
        )

    def record_observation(
        self,
        session: MultiAgentSession,
        unit_id: str,
        *,
        status: str = "SUCCESS",
        evidence_payload: dict[str, Any] | None = None,
        touched_files: tuple[str, ...] = (),
        error_message: str | None = None,
    ) -> ActionObservationV1:
        capsule = session.capsules.get(unit_id)
        if capsule is None:
            raise KeyError(f"no capsule prepared for unit {unit_id}")
        obs = ActionObservationV1(
            capsule_id=capsule.capsule_id,
            unit_id=unit_id,
            status=status,  # type: ignore[arg-type]
            evidence_payload=evidence_payload or {},
            touched_files=touched_files,
            error_message=error_message,
        )
        obs = obs.model_copy(update={"observation_hash": obs.compute_hash()})
        session.observations[unit_id] = obs
        return obs

    def transfer_context(
        self,
        session: MultiAgentSession,
        *,
        from_unit_id: str,
        to_unit_id: str,
    ) -> dict[str, Any]:
        """Context continuity across handoff: merge prior capsule + observation into next."""
        prior = session.capsules.get(from_unit_id)
        obs = session.observations.get(from_unit_id)
        transferred = {
            "from_unit_id": from_unit_id,
            "to_unit_id": to_unit_id,
            "prior_purpose": prior.purpose if prior else "",
            "prior_scope": list(prior.target_scope) if prior else [],
            "evidence_payload": dict(obs.evidence_payload) if obs else {},
            "evidence_hash": obs.observation_hash if obs else "",
        }
        return transferred

    def acquire_unit_locks(self, session: MultiAgentSession, unit: WorkUnit) -> None:
        session.lock_manager.acquire_scope_locks(unit.unit_id, unit.target_scope)

    def release_unit_locks(self, session: MultiAgentSession, unit: WorkUnit) -> None:
        session.lock_manager.release_scope_locks(unit.unit_id)


__all__ = [
    "MultiAgentProtocol",
    "MultiAgentSession",
]
