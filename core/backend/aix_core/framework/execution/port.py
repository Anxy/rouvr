"""Canonical F4 execution port around the existing real executor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from backend.aix_core.runtime.objective.contracts import (
    RepairDirectiveV1,
    StepContextCapsuleV1,
    StepContractV1,
)

if TYPE_CHECKING:
    from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


@dataclass(frozen=True, slots=True)
class ExecuteStepRequest:
    """Lossless argument carrier for ``run_real_execute``.

    It deliberately contains no execution, scheduling, retry, or verification logic.
    """

    tenant_id: str
    cycle_id: str
    objective_id: str
    objective: str
    workhub_task_id: str
    project_id: str | None
    task_number: int | None
    repository_id: str
    attachments: tuple[dict[str, Any], ...] = ()
    settings: object | None = None
    require_workhub_load: bool = True
    workspace_base: Path | None = None
    model_override: str | None = None
    caller_authorization: str | None = None
    actor_id: str = ""
    budget_config: object | None = None
    apply_edits: bool = True
    demo_capability: bool = False
    task_family: str = "UNKNOWN"
    execution_context: dict[str, Any] | None = None
    allowed_files: tuple[str, ...] | None = None
    admission_allowed_files: tuple[str, ...] | None = None
    is_cancelled: Callable[[], bool] | None = None
    step_contract: StepContractV1 | None = None
    plan_steps: tuple[StepContractV1, ...] = ()
    context_capsule: StepContextCapsuleV1 | None = None
    workspace_step_id: str | None = None
    workspace_attempt_id: str | None = None
    repair_directive: RepairDirectiveV1 | None = None
    original_objective: str | None = None
    session_id: str | None = None
    # Canonical activation/workhub binding references (lane-ops #23). These
    # are opaque refs only — never credentials — resolved and revalidated
    # against the canonical activation records by the executor before any
    # effect. Deliberately excluded from ``to_legacy_kwargs``: the legacy
    # executor has no such parameters.
    activation_id: str | None = None
    workhub_attempt_id: str | None = None
    # Immutable selection refs for exact pre-effect comparison against the
    # stored activation record. For a Muse route every carried ref must be
    # non-empty and equal the authoritative record before spawn; the
    # adapter refuses missing/mismatched refs with stable explicit codes.
    selection_id: str | None = None
    candidate_sha: str | None = None
    plan_sha256: str | None = None
    # Owner-threaded actor/worker binding and exact accepted model id
    # (lane-ops #23 M1). Opaque refs only, copied unchanged from the
    # activation record through the Temporal/command seams; the adapter
    # compares them exactly against the effect-time record. The dispatch
    # attribution in ``actor_id`` is intentionally separate and is not
    # equated with the worker binding here.
    worker_id: str | None = None
    accepted_model_id: str | None = None

    def to_legacy_kwargs(self) -> dict[str, Any]:
        """Return the exact keyword surface expected by the legacy executor."""

        return {
            "tenant_id": self.tenant_id,
            "cycle_id": self.cycle_id,
            "objective_id": self.objective_id,
            "objective": self.objective,
            "workhub_task_id": self.workhub_task_id,
            "project_id": self.project_id,
            "task_number": self.task_number,
            "repository_id": self.repository_id,
            "attachments": list(self.attachments),
            "settings": self.settings,
            "require_workhub_load": self.require_workhub_load,
            "workspace_base": self.workspace_base,
            "model_override": self.model_override,
            "caller_authorization": self.caller_authorization,
            "actor_id": self.actor_id,
            "budget_config": self.budget_config,
            "apply_edits": self.apply_edits,
            "demo_capability": self.demo_capability,
            "task_family": self.task_family,
            "execution_context": self.execution_context,
            "allowed_files": self.allowed_files,
            "admission_allowed_files": self.admission_allowed_files,
            "is_cancelled": self.is_cancelled,
            "step_contract": self.step_contract,
            "plan_steps": self.plan_steps,
            "context_capsule": self.context_capsule,
            "workspace_step_id": self.workspace_step_id,
            "workspace_attempt_id": self.workspace_attempt_id,
            "repair_directive": self.repair_directive,
            "original_objective": self.original_objective,
            "session_id": self.session_id,
        }


class ExecutionPort(Protocol):
    async def execute_step(self, request: ExecuteStepRequest) -> "RealExecuteResult": ...


__all__ = ["ExecuteStepRequest", "ExecutionPort"]
