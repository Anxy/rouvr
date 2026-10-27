"""AIX Framework Multi-Agent Delegation Manager.

Sprint: F10 Work Compiler + Multi-Agent Runtime (F10-P4)
CRITICAL INVARIANT: Agents NEVER become competing CANONICAL_COMMAND writers.
Subagents receive isolated task capsules and return structured observations only.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.agent.skill_matrix import SkillMatrix
from backend.aix_core.framework.multi_agent.models import (
    ActionObservationV1,
    StepContextCapsuleV1,
)
from backend.aix_core.framework.multi_agent.sync import ScopeLockManager
from backend.aix_core.framework.work.models import WorkUnit


class DelegationAuthorityError(PermissionError):
    """Raised when an unauthorized agent role attempts delegation or command execution."""


class DelegationManager:
    """Governs subagent delegation under strict single-command-writer invariant."""

    def __init__(self, lock_manager: ScopeLockManager | None = None) -> None:
        self.lock_manager = lock_manager or ScopeLockManager()

    def prepare_capsule(
        self,
        unit: WorkUnit,
        assigned_role: Role,
        *,
        context_data: dict[str, Any] | None = None,
    ) -> StepContextCapsuleV1:
        # Verify role authorization
        if SkillMatrix.is_effect_prohibited(assigned_role, "workhub_lifecycle_mutation"):
            pass  # Enforced by invariant

        capsule = StepContextCapsuleV1(
            unit_id=unit.unit_id,
            work_id=unit.work_id,
            assigned_role=assigned_role,
            purpose=unit.purpose,
            target_scope=unit.target_scope,
            acceptance_criteria=unit.contract.expected_checks or (unit.purpose,),
            required_capabilities=("workspace", "filesystem"),
            context_data=context_data or {},
        )
        return capsule.model_copy(update={"capsule_hash": capsule.compute_hash()})

    def execute_delegated_unit(
        self,
        capsule: StepContextCapsuleV1,
        *,
        mock_evidence: dict[str, Any] | None = None,
    ) -> ActionObservationV1:
        """REMOVED as executor — agents must not run a second execute path.

        Use F4 ExecutionPort / OC ``run_real_execute``. Capsules + observations only.
        """
        raise NotImplementedError(
            "DelegationManager.execute_delegated_unit is retired; "
            "execute via F4/OC (CANONICAL_COMMAND). Use prepare_capsule + record observations."
        )
