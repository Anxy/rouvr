"""AIX Refactored Objective Cycle API Backend.

Modular, service-oriented backend delegating to specialized domain services.
Preserves the strict authority model where AixObjectiveCycleWorkflowEngine is the sole phase writer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.objective.services.admission_service import (
    AdmissionService,
    AdmittedObjectivePackage,
)
from backend.aix_core.runtime.objective.services.dispatch_service import (
    ClaimResult,
    DispatchService,
)
from backend.aix_core.runtime.objective.services.execution_service import (
    ExecutionService,
    StepExecutionResult,
)
from backend.aix_core.runtime.objective.services.planning_service import (
    PlanningService,
)
from backend.aix_core.runtime.objective.services.repair_service import (
    RepairService,
)
from backend.aix_core.runtime.objective.services.terminalization_service import (
    TerminalizationEvaluationResult,
    TerminalizationService,
)
from backend.aix_core.runtime.objective.services.verification_service import (
    VerificationService,
)
from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)
from backend.aix_core.runtime.verification.gap import GapStore
from backend.aix_core.runtime.verification.terminalization_guard import (
    TerminalizationDecisionV1,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.workflows.temporal.objective_cycle_contracts import (
    ObjectiveCycleWorkflowInput,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
)

logger = logging.getLogger(__name__)


@dataclass
class _RefactoredObjectiveRecord:
    objective_id: str
    tenant_id: str
    actor_id: str
    intent: Any
    understanding: Any
    envelope: ObjectiveEnvelopeV1 | None
    obligations: list[ObjectiveObligationV1]
    ovc: Any | None
    task_family: str
    version: int = 1


@dataclass
class _RefactoredCycleRecord:
    cycle_id: str
    objective_id: str
    tenant_id: str
    engine: AixObjectiveCycleWorkflowEngine
    version: int = 1
    plan_revision: int = 1
    active_plan: PlanRevisionV1 | None = None
    step_runtime: dict[str, StepRuntimeStateV1] = field(default_factory=dict)
    real_execute: dict[str, Any] | None = None
    execution_claim_id: str | None = None
    execution_claim_step_id: str | None = None
    execution_claim_attempt: int = 0
    terminal_reason: str | None = None
    last_error_detail: str | None = None
    cancel_requested: bool = False
    approval_reference: str | None = None

    @property
    def phase(self) -> str:
        """Cycle phase is ALWAYS read directly from the workflow engine (Sole Phase Authority)."""
        return str(self.engine.state.phase)


class RefactoredObjectiveCycleApiBackend:
    """Service-oriented objective cycle backend adhering to the strict authority model."""

    def __init__(
        self,
        *,
        mode: str = "inprocess",
        gap_store: GapStore | None = None,
    ) -> None:
        self.mode = mode
        self.gap_store = gap_store or GapStore()
        self.admission_service = AdmissionService()
        self.planning_service = PlanningService()
        self.dispatch_service = DispatchService()
        self.execution_service = ExecutionService()
        self.verification_service = VerificationService()
        self.repair_service = RepairService(gap_store=self.gap_store)
        self.terminalization_service = TerminalizationService(gap_store=self.gap_store)

        self._objectives: dict[str, _RefactoredObjectiveRecord] = {}
        self._cycles: dict[str, _RefactoredCycleRecord] = {}
        self._plans: dict[str, PlanRevisionV1] = {}
        self._step_contracts: dict[str, dict[str, StepContractV1]] = {}

    async def command(
        self,
        *,
        operation: str,
        tenant_id: str,
        actor_id: str,
        resource_id: str | None,
        body: dict[str, Any],
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Dispatches commands to corresponding domain services."""
        if operation == "admit_objective":
            return await self._admit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                body=body,
                command_id=idempotency_key or "admit",
            )
        elif operation == "start_cycle":
            return await self._start_cycle(
                tenant_id=tenant_id,
                actor_id=actor_id,
                objective_id=str(resource_id or ""),
                body=body,
                command_id=idempotency_key or "start",
            )
        elif operation in (
            "validate_approval_reference",
            "resume",
            "cancel",
            "block",
            "reject",
            "repair_cycle",
            "reverify_and_terminalize",
        ):
            return await self._control(
                tenant_id=tenant_id,
                cycle_id=str(resource_id or ""),
                expected_version=expected_version,
                action=operation,
                body=body,
                command_id=idempotency_key or "control",
                actor_id=actor_id,
            )
        return {
            "ok": False,
            "operation": operation,
            "error": {"code": "UNKNOWN_OPERATION", "detail": f"Operation {operation} not supported"},
        }

    async def _admit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        body: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        admitted = self.admission_service.admit(
            tenant_id=tenant_id,
            actor_id=actor_id,
            body=body,
            command_id=command_id,
        )
        rec = _RefactoredObjectiveRecord(
            objective_id=admitted.objective_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            intent=admitted.intent,
            understanding=admitted.understanding,
            envelope=admitted.envelope,
            obligations=admitted.obligations,
            ovc=admitted.ovc,
            task_family=admitted.task_family,
        )
        self._objectives[admitted.objective_id] = rec
        return {
            "ok": True,
            "operation": "admit_objective",
            "tenant_id": tenant_id,
            "objective_id": admitted.objective_id,
            "version": rec.version,
            "obligations": [o.model_dump() for o in admitted.obligations],
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
        }

    async def _start_cycle(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        objective_id: str,
        body: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        obj = self._objectives.get(objective_id)
        if obj is None or obj.tenant_id != tenant_id:
            return {"ok": False, "operation": "start_cycle", "error": {"code": "OBJECTIVE_NOT_FOUND"}}

        import hashlib
        from backend.aix_core.runtime.objective.ulid_compat import new_ulid
        cycle_id = f"cyc_{new_ulid()[:12]}"
        candidate_sha = str(body.get("candidate_sha") or "main-commit")
        if len(candidate_sha) < 7:
            candidate_sha = candidate_sha.ljust(7, "0")
        plan_material = f"{objective_id}:{cycle_id}:{candidate_sha}"
        plan_sha256 = hashlib.sha256(plan_material.encode("utf-8")).hexdigest()

        wf_input = ObjectiveCycleWorkflowInput(
            workflow_version=1,
            tenant_id=tenant_id,
            objective_id=objective_id,
            cycle_id=cycle_id,
            candidate_sha=candidate_sha,
            plan_sha256=plan_sha256,
            start_request_id=command_id,
            correlation_id=command_id,
        )
        engine = AixObjectiveCycleWorkflowEngine(wf_input)
        engine.run_until_wait_or_terminal()

        cycle = _RefactoredCycleRecord(
            cycle_id=cycle_id,
            objective_id=objective_id,
            tenant_id=tenant_id,
            engine=engine,
            version=2,
        )
        self._cycles[cycle_id] = cycle

        return {
            "ok": True,
            "operation": "start_cycle",
            "tenant_id": tenant_id,
            "objective_id": objective_id,
            "cycle_id": cycle_id,
            "phase": cycle.phase,
            "version": cycle.version,
            "status": engine.query_status(),
            "workhub_lifecycle_authority": False,
        }

    async def _control(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        expected_version: int | None,
        action: str,
        body: dict[str, Any],
        command_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        cycle = self._cycles.get(cycle_id)
        if cycle is None or cycle.tenant_id != tenant_id:
            return {"ok": False, "operation": action, "error": {"code": "CYCLE_NOT_FOUND"}}
        if expected_version is not None and expected_version != cycle.version:
            return {
                "ok": False,
                "operation": action,
                "error": {"code": "VERSION_CONFLICT", "expected": expected_version, "actual": cycle.version},
            }

        engine = cycle.engine
        if action == "cancel":
            cycle.cancel_requested = True
            engine.signal_cancel(reason=str(body.get("reason") or "OPERATOR_CANCEL"))
            cycle.terminal_reason = "CANCELLED"
            return {
                "ok": True,
                "operation": "cancel",
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
            }
        elif action == "reject":
            engine.signal_cancel(reason=str(body.get("reason") or "OPERATOR_REJECT"))
            cycle.terminal_reason = "REJECTED"
            return {
                "ok": True,
                "operation": "reject",
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "terminal_reason": cycle.terminal_reason,
            }
        elif action == "block":
            engine.wait_blocker()
            return {
                "ok": True,
                "operation": "block",
                "cycle_id": cycle_id,
                "phase": cycle.phase,
            }
        elif action == "validate_approval_reference":
            ref = str(body.get("approval_reference") or "")
            engine.signal_approval(approval_ref=ref, valid=bool(ref.strip()))
            cycle.approval_reference = ref or None
            cycle.version += 1

            # When approved into RUNNING_STEP, run terminalization evaluation
            if engine.state.phase == "RUNNING_STEP":
                obj = self._objectives.get(cycle.objective_id)
                eval_res = self.terminalization_service.evaluate_guards(
                    objective_id=cycle.objective_id,
                    cycle_id=cycle.cycle_id,
                    version=cycle.version,
                    current_phase=cycle.phase,
                    plan=cycle.active_plan,
                    step_runtime=cycle.step_runtime,
                    ovc=obj.ovc if obj else None,
                    real_execute=cycle.real_execute,
                )
                self.terminalization_service.apply_terminalization_to_engine(
                    engine=engine,
                    step_id=cycle.execution_claim_step_id or "step-1",
                    evaluation=eval_res,
                )

            return {
                "ok": True,
                "operation": "validate_approval_reference",
                "cycle_id": cycle_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "status": engine.query_status(),
                "workhub_lifecycle_authority": False,
            }

        return {"ok": False, "operation": action, "error": {"code": "ACTION_NOT_HANDLED"}}

    async def query(
        self,
        *,
        query_type: str,
        tenant_id: str,
        resource_id: str | None,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queries cycle or objective status."""
        if query_type == "get_cycle":
            cycle = self._cycles.get(str(resource_id or ""))
            if cycle is None or cycle.tenant_id != tenant_id:
                return {"ok": False, "error": {"code": "CYCLE_NOT_FOUND"}}
            return {
                "ok": True,
                "cycle_id": cycle.cycle_id,
                "objective_id": cycle.objective_id,
                "phase": cycle.phase,
                "version": cycle.version,
                "status": cycle.engine.query_status(),
                "workhub_lifecycle_authority": False,
            }
        elif query_type == "get_objective":
            obj = self._objectives.get(str(resource_id or ""))
            if obj is None or obj.tenant_id != tenant_id:
                return {"ok": False, "error": {"code": "OBJECTIVE_NOT_FOUND"}}
            return {
                "ok": True,
                "objective_id": obj.objective_id,
                "tenant_id": obj.tenant_id,
                "version": obj.version,
                "obligations": [o.model_dump() for o in obj.obligations],
            }
        return {"ok": False, "error": {"code": "UNKNOWN_QUERY_TYPE"}}
