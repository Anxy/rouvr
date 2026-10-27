"""EX2-S008: gateway HMAC on kernel routes + live objective_cycle dispatch bind."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.cycle_adapter import (
    ObjectiveCycleExecutorAdapter,
    outcome_from_cycle_view,
)
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.aix_core.runtime.objective.api_security import (
    CycleApiSecurityPolicy,
    CyclePermission,
    sign_gateway_assertion,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.routers.execution_kernel_router import router as execution_kernel_router


def _gateway_headers(
    *,
    secret: bytes = b"kernel-secret",
    tenant_id: str = "t1",
    actor_id: str = "actor-1",
    request_id: str = "req-kernel-1",
    roles: tuple[str, ...] | None = None,
) -> dict[str, str]:
    issued_at = int(time.time())
    role_tuple = roles or (CyclePermission.READ.value, CyclePermission.COMMAND.value)
    signature = sign_gateway_assertion(
        secret=secret,
        gateway_id="aix-bff",
        tenant_id=tenant_id,
        actor_id=actor_id,
        roles=role_tuple,
        issued_at_epoch=issued_at,
        request_id=request_id,
    )
    return {
        "Authorization": "Bearer local-token",
        "X-AIX-Gateway-Identity": "aix-bff",
        "X-Tenant-ID": tenant_id,
        "X-AIX-Actor-ID": actor_id,
        "X-AIX-Roles": ",".join(role_tuple),
        "X-AIX-Gateway-Issued-At": str(issued_at),
        "X-Request-ID": request_id,
        "X-AIX-Gateway-Signature": signature,
    }


def test_waiting_approval_maps_to_blocked() -> None:
    outcome = outcome_from_cycle_view(cycle_id="cyc_1", phase="WAITING_APPROVAL")
    assert outcome is not None
    assert outcome.kind == "BLOCKED"
    assert outcome.reason_code == "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_kernel_gateway_hmac_required_when_policy_attached() -> None:
    app = FastAPI()
    app.state.execution_kernel_service = KernelService()
    app.state.execution_kernel_require_gateway = True
    app.state.objective_cycle_security_policy = CycleApiSecurityPolicy(
        secret=b"kernel-secret",
        allowed_gateway_ids=["aix-bff"],
        max_assertion_age_seconds=300,
    )
    app.include_router(execution_kernel_router)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/aix/execution-kernel/health")
        assert health.status_code == 200
        assert health.json()["gateway_required"] is True

        denied = await client.post(
            "/aix/execution-kernel/events",
            headers={"X-Tenant-ID": "t1"},
            json={
                "aggregate_type": "project",
                "aggregate_id": "p1",
                "expected_sequence_number": 1,
                "event_type": "PROJECT_CREATED",
                "command_id": "c1",
                "payload": {"status": "ACTIVE"},
            },
        )
        assert denied.status_code == 401

        ok = await client.post(
            "/aix/execution-kernel/events",
            headers=_gateway_headers(request_id="req-ok-1"),
            json={
                "aggregate_type": "project",
                "aggregate_id": "p1",
                "expected_sequence_number": 1,
                "event_type": "PROJECT_CREATED",
                "command_id": "c1",
                "payload": {"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"},
            },
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["ok"] is True


@pytest.mark.asyncio
async def test_live_dispatch_binds_objective_cycle() -> None:
    svc = KernelService()
    for body in (
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "name": "C", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "Ship page"}),
        ("work_item", "wi_1", 1, "WI_CREATED", "c4", {"status": "PENDING", "parent_id": "pr_1", "title": "Build landing"}),
    ):
        await svc.append_and_project(
            tenant_id="t1",
            aggregate_type=body[0],
            aggregate_id=body[1],
            expected_sequence_number=body[2],
            event_type=body[3],
            command_id=body[4],
            payload=body[5],
        )

    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await svc.live_dispatch(
        tenant_id="t1",
        actor_id="actor-1",
        work_item_id="wi_1",
        backend=backend,
        workhub_task_id="wh-task-kernel-1",
        idempotency_key="live-1",
        repository_id="aix",
        candidate_sha="abc1234",
        expected_sequence_number=2,
        mark_in_progress=True,
        repository={"repository_id": "aix", "current_commit_sha": "abc1234"},
    )
    assert result["handle"]["cycle_id"].startswith("cyc_")
    assert result["handle"]["objective_id"].startswith("obj_")
    assert result["event"]["event_type"] == "WI_IN_PROGRESS"
    # Fresh start lands in WAITING_APPROVAL → BLOCKED provisional outcome (not VERIFIED).
    assert result["outcome"] is not None
    assert result["outcome"]["kind"] == "BLOCKED"
    assert result["outcome"]["reason_code"] == "WAITING_APPROVAL"

    polled = await svc.poll_live_dispatch(
        tenant_id="t1",
        backend=backend,
        handle=AdapterHandle_from_dict(result["handle"]),
    )
    assert polled is not None
    assert polled["kind"] == "BLOCKED"


def AdapterHandle_from_dict(data: dict):
    from backend.aix_core.runtime.executor.execution_kernel_adapter.cycle_adapter import AdapterHandle

    return AdapterHandle(
        cycle_id=data["cycle_id"],
        objective_id=data["objective_id"],
        phase=data["phase"],
        work_item_id=data["work_item_id"],
        tenant_id=data["tenant_id"],
        capsule_content_sha256=data["capsule_content_sha256"],
        candidate_sha=data["candidate_sha"],
    )


@pytest.mark.asyncio
async def test_adapter_dispatch_unit() -> None:
    from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import build_dispatch_request

    svc = KernelService()
    await svc.append_and_project(
        tenant_id="t1",
        aggregate_type="project",
        aggregate_id="proj_1",
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id="c1",
        payload={"status": "ACTIVE", "vision_summary": "V", "requirements_summary": "R"},
    )
    await svc.append_and_project(
        tenant_id="t1",
        aggregate_type="capability",
        aggregate_id="cap_1",
        expected_sequence_number=1,
        event_type="CAPABILITY_CREATED",
        command_id="c2",
        payload={"status": "ACTIVE", "name": "C", "parent_id": "proj_1"},
    )
    await svc.append_and_project(
        tenant_id="t1",
        aggregate_type="problem",
        aggregate_id="pr_1",
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id="c3",
        payload={"status": "EXECUTING", "parent_id": "cap_1", "charter_summary": "X"},
    )
    await svc.append_and_project(
        tenant_id="t1",
        aggregate_type="work_item",
        aggregate_id="wi_1",
        expected_sequence_number=1,
        event_type="WI_CREATED",
        command_id="c4",
        payload={"status": "PENDING", "parent_id": "pr_1", "title": "T"},
    )
    capsule = await svc.build_capsule(
        tenant_id="t1",
        work_item_id="wi_1",
        repository={"repository_id": "aix", "current_commit_sha": "deadbeef"},
    )
    dispatch = build_dispatch_request(capsule=capsule)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    adapter = ObjectiveCycleExecutorAdapter(backend)
    handle, outcome = await adapter.dispatch(
        tenant_id="t1",
        actor_id="a1",
        capsule=capsule,
        dispatch=dispatch,
        workhub_task_id="wh-1",
        idempotency_key="u1",
        candidate_sha="deadbee",
    )
    assert handle.cycle_id
    assert outcome is not None and outcome.kind == "BLOCKED"
