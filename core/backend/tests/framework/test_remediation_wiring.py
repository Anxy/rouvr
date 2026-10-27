"""Remediation R7 — wiring tests through real runtime entry points.

Proves ON/OFF / fail-closed behavior for audited surfaces. Does not claim
framework packages are OC-authoritative unless the branch is exercised here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from backend.aix_core.framework.agent.binding import AgentBinding
from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.aix_core.framework.integration.hyeve.resolver import get_hyeve_capability_resolver
from backend.aix_core.framework.integration.workhub.boundary import WorkHubBoundary, WorkHubOperation
from backend.aix_core.framework.integration.workhub.client import (
    WorkHubAuthorityDenied,
    WorkHubTaskClient,
    assert_workhub_write_allowed,
)
from backend.aix_core.framework.recovery.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEventV1,
    ObjectiveCycleCheckpointStore,
)
from backend.aix_core.framework.recovery.escalation import AixEscalationPolicyAdapter, EscalationRequest
from backend.aix_core.framework.recovery.resume import ResumeProtocol, ResumeRequest
from backend.aix_core.framework.verification.catalog import build_default_catalog
from backend.aix_core.framework.verification.contract import GateContext, VerificationContract
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1, UnderstandingEvidenceV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot, settings


class RecordingPort:
    def __init__(self) -> None:
        self.requests: list[ExecuteStepRequest] = []

    async def execute_step(self, request: ExecuteStepRequest) -> RealExecuteResult:
        self.requests.append(request)
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace="ws",
            evidence_refs=["file://ws/evidence/report.json"],
            workhub_loaded=False,
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
            completion_gates={"diff_exists": True},
        )


def _seed(backend: DurableObjectiveCycleApiBackend, objective_id: str, cycle_id: str) -> None:
    backend._understanding_evidence[objective_id] = UnderstandingEvidenceV1(  # noqa: SLF001
        objective_id=objective_id,
        cycle_id=cycle_id,
        files_read=("helper.py",),
        observed_problem="test evidence",
        understanding_complete=True,
    )
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose="seeded execute",
        target_scope=("helper.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("helper works",),
        validation_commands=("python -m py_compile helper.py",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        obligation_refs=tuple(o.id for o in backend._obligations.get(objective_id, ())),  # noqa: SLF001
        status="PENDING",
    )


async def _admit_start_approve(backend: DurableObjectiveCycleApiBackend) -> tuple[str, str, int]:
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-1",
            "objective": "Add a helper function",
            "task_class": "single_file_helper",
            "definition_of_done": ("done",),
            "repository_id": "aix-frontend",
            "allowed_files": (),
            "project_id": "proj-1",
        },
        idempotency_key="admit-wire",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-wire",
        expected_version=None,
    )
    _seed(backend, admit["objective_id"], started["cycle_id"])
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/local"},
        idempotency_key="approve-wire",
        expected_version=started["version"],
    )
    assert approved["ok"] is True, approved
    return admit["objective_id"], started["cycle_id"], int(started["version"])


@pytest.mark.asyncio
async def test_execution_port_on_routes_through_port() -> None:
    port = RecordingPort()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        execution_port=port,
        framework_flags=FrameworkFlagsSnapshot(execution_port=True),
    )
    await _admit_start_approve(backend)
    assert len(port.requests) == 1


@pytest.mark.asyncio
async def test_execution_port_off_bypasses_port_uses_run_real_execute() -> None:
    port = RecordingPort()
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        execution_port=port,
        framework_flags=FrameworkFlagsSnapshot(execution_port=False),
    )
    fake = RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace="ws",
        evidence_refs=["file://ws/evidence/report.json"],
        workhub_loaded=False,
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=1,
        target_files_modified=1,
        completion_gates={"diff_exists": True},
    )
    with patch(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        new=AsyncMock(return_value=fake),
    ) as mocked:
        await _admit_start_approve(backend)
        assert mocked.await_count == 1
    assert port.requests == []


def test_verification_catalog_flag_reserved_does_not_alter_live_authority() -> None:
    """Catalog exists and uses real authorities; OC authority remains evaluate_completion_gates /
    contract_coverage — flipping RESERVED catalog flag must not invent PASS without contract.
    """
    assert bool(getattr(settings, "AIX_FRAMEWORK_VERIFICATION_CATALOG", False)) is False
    catalog = build_default_catalog()
    ctx = GateContext(
        contract=VerificationContract(
            contract_id="c1",
            objective_id="o1",
            cycle_id="c1",
            step_id="s1",
            criteria=(),
            expected_checks=(),
            required_evidence=(),
            continuity_required=False,
        ),
        outcome_contract=None,
    )
    coverage_gate = next(g for g in catalog.ordered_for(ctx) if g.gate_id == "contract_coverage")
    result = coverage_gate.evaluator(ctx)
    # No sealed outcome contract → require=False path; still not a stub always-PASS inventing coverage.
    live = evaluate_contract_coverage(
        outcome_contract=None,
        steps=(),
        require_outcome_contract=False,
    )
    assert result.passed is bool(live.ok)


def test_workhub_boundary_default_denies_lifecycle_writes() -> None:
    assert bool(getattr(settings, "AIX_ALLOW_WORKHUB_LIFECYCLE_WRITES", True)) is False
    with pytest.raises(WorkHubAuthorityDenied):
        assert_workhub_write_allowed(WorkHubOperation.TASK_CREATE)
    with pytest.raises(WorkHubAuthorityDenied):
        assert_workhub_write_allowed(WorkHubOperation.STATE_TRANSITION)
    # Evidence / verification remain authorized attach paths
    assert_workhub_write_allowed(WorkHubOperation.EVIDENCE_ATTACH)
    assert_workhub_write_allowed(WorkHubOperation.VERIFICATION_RECORD)


@pytest.mark.asyncio
async def test_workhub_client_create_task_enforces_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK", False, raising=False)
    monkeypatch.setattr(settings, "AIX_ALLOW_WORKHUB_LIFECYCLE_WRITES", False, raising=False)
    client = WorkHubTaskClient(base_url="http://localhost:9", service_token="t")
    with pytest.raises(WorkHubAuthorityDenied):
        await client.create_task(
            project_id="p1",
            title="x",
            description="y",
            tenant_id="t1",
        )


@pytest.mark.asyncio
async def test_workhub_client_transition_allowed_when_lifecycle_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OBJECTIVE_CYCLE_MARK_WORKHUB_DONE", False, raising=False)
    monkeypatch.setattr(settings, "AIX_ALLOW_WORKHUB_LIFECYCLE_WRITES", True, raising=False)
    client = WorkHubTaskClient(base_url="http://localhost:9", service_token="t")

    class _Resp:
        is_error = False

        def json(self) -> dict[str, Any]:
            return {"data": {"status": "running", "id": "task-1"}}

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> _Resp:
            return _Resp()

    with patch("backend.aix_core.framework.integration.workhub.client.httpx.AsyncClient", _Client):
        out = await client.transition_task(
            project_id="p1",
            task_id="task-1",
            status_value="running",
            reason="test",
            tenant_id="t1",
        )
    assert out["status"] == "running"


@pytest.mark.asyncio
async def test_recovery_checkpoint_resume_roundtrip() -> None:
    store = ObjectiveCycleCheckpointStore()
    recorded_at = "2026-08-29T12:00:00Z"
    workflow = {"phase": "POST_ATTEMPT_COMMIT", "engine_version": 1}
    dispatcher = {"state": "POST_ATTEMPT_COMMIT", "step_id": "step-1"}
    plan = {"plan_id": "plan-1", "revision": 1}
    budgets = {"retries_remaining": 2, "replans_remaining": 1}
    evidence_refs = ("sha256:ref1",)
    decision_state = {"cursor": "step-1"}
    state_hash = CheckpointEventV1.compute_state_hash(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checkpoint_id="chk_wire",
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=1,
        command_id="cmd-1:checkpoint",
        reason="POST_ATTEMPT_COMMIT",
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=None,
        budgets=budgets,
        approval=None,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
    )
    chk = CheckpointEventV1(
        checkpoint_id="chk_wire",
        tenant_id="tenant-1",
        objective_id="obj-1",
        cycle_id="cycle-1",
        event_sequence=1,
        command_id="cmd-1:checkpoint",
        reason="POST_ATTEMPT_COMMIT",
        recorded_at=recorded_at,
        workflow=workflow,
        dispatcher=dispatcher,
        plan=plan,
        attempt=None,
        budgets=budgets,
        approval=None,
        evidence_refs=evidence_refs,
        decision_state=decision_state,
        state_hash=state_hash,
    )
    await store.append(chk)
    from backend.aix_core.framework.recovery.resume import ResumeDisposition

    protocol = ResumeProtocol(checkpoint_store=store)
    result = await protocol.resume(
        ResumeRequest(
            tenant_id="tenant-1",
            cycle_id="cycle-1",
            checkpoint_id="chk_wire",
            command_id="cmd-resume-wire",
        )
    )
    assert result.disposition == ResumeDisposition.APPLIED
    assert result.checkpoint_id == "chk_wire"


@pytest.mark.asyncio
async def test_escalation_fail_closed_without_service() -> None:
    adapter = AixEscalationPolicyAdapter()
    receipt = await adapter.notify_operator(
        EscalationRequest(
            tenant_id="t",
            objective_id="o",
            cycle_id="c",
            reason_code="BUDGET",
        )
    )
    assert receipt.status != "DELIVERED"
    assert receipt.status == "FAILED_NO_SERVICE"


def test_agent_binding_uses_governed_router() -> None:
    binding = AgentBinding.for_role(Role.PLANNER)
    assert binding.agent.role == Role.PLANNER
    assert binding.model_ref
    assert binding.route_decision.disposition == "ALLOW"


def test_runtime_agent_registry_canonical_for_handoff() -> None:
    from backend.aix_core.runtime.agents.agent_registry import AGENT_SPECS, get_agent_spec

    assert len(AGENT_SPECS) == 8
    assert get_agent_spec("planner") is not None


def test_hyeve_capability_routing_resolver() -> None:
    resolver = get_hyeve_capability_resolver()
    llm = resolver.resolve("llm.chat_completion")
    assert llm is not None
    assert llm.capability_id == "llm.chat_completion"
    assert resolver.resolve("does.not.exist") is None


def test_workhub_lifecycle_master_flag_allows_create() -> None:
    d = WorkHubBoundary.evaluate_operation(
        WorkHubOperation.TASK_CREATE,
        create_task_flag=False,
        lifecycle_writes_enabled=True,
    )
    assert d.allowed is True
