"""Tests for durable objective-cycle API backend wiring."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_core.runtime.objective.api_security import (
    CyclePermission,
    sign_gateway_assertion,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.runtime_attach import (
    attach_objective_cycle_runtime,
    build_objective_cycle_security_policy,
)
from backend.routers.objective_cycle_router import router


class _Settings:
    OBJECTIVE_CYCLE_GATEWAY_SECRET = "local-cert-secret"
    OBJECTIVE_CYCLE_ALLOWED_GATEWAYS = "aix-bff"
    OBJECTIVE_CYCLE_ASSERTION_MAX_AGE_SECONDS = 300
    OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER = True
    OBJECTIVE_CYCLE_PREFER_POSTGRES = False
    DATABASE_URL = ""


def _gateway_headers(*, tenant_id: str = "tenant-1", actor_id: str = "human-1", request_id: str = "req-1") -> dict[str, str]:
    issued_at = int(time.time())
    roles = (CyclePermission.READ.value, CyclePermission.COMMAND.value)
    signature = sign_gateway_assertion(
        secret=b"local-cert-secret",
        gateway_id="aix-bff",
        tenant_id=tenant_id,
        actor_id=actor_id,
        roles=roles,
        issued_at_epoch=issued_at,
        request_id=request_id,
    )
    return {
        "Authorization": "Bearer local-token",
        "X-AIX-Gateway-Identity": "aix-bff",
        "X-Tenant-ID": tenant_id,
        "X-AIX-Actor-ID": actor_id,
        "X-AIX-Roles": ",".join(roles),
        "X-AIX-Gateway-Issued-At": str(issued_at),
        "X-Request-ID": request_id,
        "X-AIX-Gateway-Signature": signature,
    }


@pytest.mark.asyncio
async def test_attach_wires_security_and_inprocess_backend() -> None:
    app = FastAPI()
    status = await attach_objective_cycle_runtime(app, _Settings())
    assert status["security_policy"] is True
    assert status["api_backend"] is True
    assert status["backend_mode"] == "inprocess"
    assert getattr(app.state, "objective_cycle_api_backend", None) is not None
    assert getattr(app.state, "objective_cycle_security_policy", None) is not None


@pytest.mark.asyncio
async def test_backend_complete_reject_block_cancel() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Prove local cycle wiring",
            "task_class": "bounded_repair",
            "definition_of_done": ("tests pass",),
            "repository_id": "aix",
            # Empty admission is NEED_EVIDENCE; wiring must ground Understanding.
            "allowed_files": ("README.md",),
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    assert admit["ok"] is True
    objective_id = admit["objective_id"]

    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-1",
        expected_version=None,
    )
    assert started["ok"] is True
    assert started["phase"] == "WAITING_APPROVAL"
    cycle_id = started["cycle_id"]
    version = started["version"]

    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=cycle_id,
        body={"approval_reference": "workhub://approvals/approval-1"},
        idempotency_key="approve-1",
        expected_version=version,
    )
    assert approved["ok"] is True
    assert approved["phase"] == "COMPLETED_FOR_REVIEW"

    # reject path on a fresh cycle
    started2 = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1235", "budgets": {}},
        idempotency_key="start-2",
        expected_version=None,
    )
    rejected = await backend.command(
        operation="reject_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started2["cycle_id"],
        body={"reason": "operator_reject"},
        idempotency_key="reject-1",
        expected_version=started2["version"],
    )
    assert rejected["ok"] is True
    assert rejected["phase"] == "CANCELLED"
    assert rejected["terminal_reason"] == "REJECTED"

    started3 = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1236", "budgets": {}},
        idempotency_key="start-3",
        expected_version=None,
    )
    blocked = await backend.command(
        operation="block_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started3["cycle_id"],
        body={"reason": "missing_context"},
        idempotency_key="block-1",
        expected_version=started3["version"],
    )
    assert blocked["ok"] is True
    assert blocked["phase"] == "BLOCKED"

    started4 = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=objective_id,
        body={"candidate_sha": "abc1237", "budgets": {}},
        idempotency_key="start-4",
        expected_version=None,
    )
    cancelled = await backend.command(
        operation="cancel_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started4["cycle_id"],
        body={"reason": "operator_cancel"},
        idempotency_key="cancel-1",
        expected_version=started4["version"],
    )
    assert cancelled["ok"] is True
    assert cancelled["phase"] == "CANCELLED"


def test_router_http_admit_no_longer_503_when_wired() -> None:
    import asyncio

    app = FastAPI()
    app.include_router(router)
    asyncio.run(attach_objective_cycle_runtime(app, _Settings()))
    client = TestClient(app)
    headers = _gateway_headers(request_id="http-admit-1")
    response = client.post(
        "/aix/objectives/admit",
        headers={**headers, "Idempotency-Key": "http-admit-1"},
        json={
            "workhub_task_id": "wh-task-http",
            "objective": "HTTP admit wiring",
            "task_class": "bounded_repair",
            "definition_of_done": ["wired"],
            "repository_id": "aix",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    assert body["ok"] is True
    assert body["objective_id"]


def test_security_policy_builder_requires_secret(monkeypatch) -> None:
    monkeypatch.delenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_GATEWAY_SECRET", raising=False)

    class Empty:
        OBJECTIVE_CYCLE_GATEWAY_SECRET = None
        OBJECTIVE_CYCLE_ALLOWED_GATEWAYS = "aix-bff"
        OBJECTIVE_CYCLE_ASSERTION_MAX_AGE_SECONDS = 60

    assert build_objective_cycle_security_policy(Empty()) is None
