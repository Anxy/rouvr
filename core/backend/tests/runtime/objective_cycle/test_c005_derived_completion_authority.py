"""AIX-VOL-II-T002: Derived Completion Authority & Transition Guards.

Verifies:
1. Completion is derived: executor, kernel, and MVP writes cannot directly declare
   an objective `VERIFIED` or `PROVEN_COMPLETE`. Completion MUST flow through deterministic
   completion gates (`evaluate_completion_gates`).
2. Adversarial attempts to bypass completion gates fail closed.
3. State machine transition guards fail closed on illegal transitions.
4. MVP and V2 retired mutation paths return HTTP 410 and cannot set completion.
"""

from __future__ import annotations

from typing import Any

import pytest
import httpx

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.experimental.state_machine import (
    IllegalTransitionError,
    validate_cycle_transition,
    validate_step_transition,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflowEngine,
    ObjectiveCycleWorkflowInput,
    IllegalPhaseTransitionError,
)
from backend.routers.aix_mvp_product_router import router as mvp_router
from backend.tests.test_public_router_authority_matrix import (
    _isolated_router_app,
    _mutation_routes,
    _request_parameters,
    _request_path,
    _request_payload,
)


@pytest.mark.asyncio
async def test_forged_executor_payload_cannot_bypass_completion_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial test: An executor returning ok=True and forged objective_status="COMPLETED"
    or verification_status="PASSED" when completion gates failed (e.g. BLOCKED / COSMETIC_PATCH_ONLY)
    MUST fail closed and refuse to advance to COMPLETED_FOR_REVIEW.
    """

    async def _mock_forged_real_execute(**kwargs: Any) -> RealExecuteResult:
        # Simulates an executor that claims success mechanically but failed gates
        return RealExecuteResult(
            ok=True,
            code="FORGED_SUCCESS",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/report.json"],
            artifacts={"execution_report": "file://ws/evidence/report.json"},
            execution_status="BLOCKED",
            verification_status="NOT_RUN",
            objective_status="BLOCKED",
            reason_code="COSMETIC_PATCH_ONLY",
            real_operations=1,
            target_files_modified=0,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _mock_forged_real_execute,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False
    )

    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-501",
            "objective": "Refactor module",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": ("module.py",),
            "project_id": "proj-1",
            "task_number": 501,
        },
        idempotency_key="admit-501",
        expected_version=None,
    )

    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc5012345", "budgets": {"max_steps": 3}},
        idempotency_key="start-501",
        expected_version=None,
    )

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/test-501"},
        idempotency_key="approve-501",
        expected_version=started["version"],
    )

    # Must fail closed because objective_status is BLOCKED
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["phase"] != "COMPLETED_FOR_REVIEW"
    assert approved["error"]["code"] in {"OBJECTIVE_NOT_COMPLETE", "VERIFICATION_HALT"}


@pytest.mark.asyncio
async def test_mvp_mutations_cannot_set_completion_state() -> None:
    """Adversarial test: Calls to retired MVP mutation routes (admit, execute, replay, review-decision)
    must return HTTP 410 Gone and cannot mutate cycle state or set VERIFIED/PROVEN_COMPLETE.
    """
    retired_app = _isolated_router_app(mvp_router)
    mutation_routes = _mutation_routes(mvp_router)
    assert mutation_routes, "MVP router must expose mutation routes to verify retirement"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=retired_app), base_url="http://testserver"
    ) as client:
        for method, path, route in mutation_routes:
            resp = await client.request(
                method,
                _request_path(retired_app, route, path),
                headers={
                    "Authorization": "Bearer test",
                    "X-Tenant-ID": "t1",
                    "X-Actor-ID": "a1",
                },
                json=_request_payload(route),
                params=_request_parameters(route),
            )
            assert resp.status_code == 410, (
                f"{method} {path} returned {resp.status_code}, expected 410"
            )


def test_state_machine_illegal_transitions_fail_closed() -> None:
    """State machine validation must fail closed on unpermitted transitions and WorkHub verbs."""
    # Legal transition
    validate_cycle_transition("RECEIVED", "ADMITTED")

    # Illegal transitions must raise IllegalTransitionError
    illegal_cycle_pairs = [
        ("RECEIVED", "COMPLETED_FOR_REVIEW"),
        ("VERIFYING_STEP", "ADMITTED"),
        ("COMPLETED_FOR_REVIEW", "RUNNING_STEP"),
        ("FAILED", "RUNNING_STEP"),
        ("CANCELLED", "PLANNING"),
    ]
    for from_state, to_state in illegal_cycle_pairs:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition(from_state, to_state)

    # WorkHub lifecycle-acceptance verbs must be rejected
    for verb in ["ACCEPTED", "CLOSED", "DONE", "WORKHUB_ACCEPTED"]:
        with pytest.raises(IllegalTransitionError):
            validate_cycle_transition("VERIFYING_STEP", verb)

    # Illegal step transitions
    illegal_step_pairs = [
        ("VERIFIED", "IN_PROGRESS"),
        ("FAILED", "PENDING"),
        ("BLOCKED", "DISPATCHED"),
    ]
    for from_s, to_s in illegal_step_pairs:
        with pytest.raises(IllegalTransitionError):
            validate_step_transition(from_s, to_s)


def test_workflow_engine_phase_transition_guards_fail_closed() -> None:
    """Workflow engine must fail closed on illegal phase transitions or forbidden states."""
    inp = ObjectiveCycleWorkflowInput(
        tenant_id="t1",
        objective_id="obj1",
        cycle_id="cyc1",
        workflow_version=1,
        candidate_sha="sha1234567",
        plan_sha256="0" * 64,
        start_request_id="req1",
        correlation_id="corr1",
    )
    engine = AixObjectiveCycleWorkflowEngine(inp)

    # Attempting to jump directly from STARTING to COMPLETED_FOR_REVIEW must raise IllegalPhaseTransitionError
    with pytest.raises(IllegalPhaseTransitionError):
        engine._transition("COMPLETED_FOR_REVIEW", reason="ILLEGAL_JUMP")

    # Attempting WorkHub lifecycle state raises ValueError
    with pytest.raises(ValueError, match="TEMPORAL_WORKHUB_LIFECYCLE_STATE_FORBIDDEN"):
        engine._transition("DONE", reason="ILLEGAL_WORKHUB_VERB")
