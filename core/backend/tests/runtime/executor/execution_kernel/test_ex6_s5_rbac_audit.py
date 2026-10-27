"""AIX-EXECUTION-006-S5: RBAC and Audit.

A zero-trust audit spot-checked and confirmed: `POST /learning`,
`/learning/{id}/advance`, `/scope`, `/confidence`, `/reject` were missing
the `_require_command` RBAC gate that every other mutating endpoint in this
router already enforces — a caller holding only READ could mutate learning
records through them. (A fresh re-check for this sprint found one more the
audit missed: none of the other 4 flagged endpoints — semantic/search,
planning/decompose, planning/autonomy, reasoning/query — actually mutate
anything; their own docstrings say so and their service methods only read.
Gating those would be over-restrictive, not a fix, so they were left alone.)

These tests use a REAL gateway security policy with an explicit, narrow
roles tuple — not the no-policy fallback other tests in this file's sibling
suite use, which hardcodes (READ, COMMAND, ADMIN) for any caller and so
cannot exercise this distinction at all.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.aix_core.runtime.objective.api_security import (
    CycleApiSecurityContext,
    CyclePermission,
    CycleApiSecurityPolicy,
    sign_gateway_assertion,
)
from backend.routers.execution_kernel_router import router as execution_kernel_router

_GATEWAY_SECRET = b"s5-test-secret"
_TENANT = "t_s5"


def _headers(*, roles: tuple[str, ...], request_id: str) -> dict[str, str]:
    issued_at = int(time.time())
    signature = sign_gateway_assertion(
        secret=_GATEWAY_SECRET,
        gateway_id="aix-bff",
        tenant_id=_TENANT,
        actor_id="s5-actor",
        roles=roles,
        issued_at_epoch=issued_at,
        request_id=request_id,
    )
    return {
        "Authorization": "Bearer local-token",
        "X-AIX-Gateway-Identity": "aix-bff",
        "X-Tenant-ID": _TENANT,
        "X-AIX-Actor-ID": "s5-actor",
        "X-AIX-Roles": ",".join(roles),
        "X-AIX-Gateway-Issued-At": str(issued_at),
        "X-Request-ID": request_id,
        "X-AIX-Gateway-Signature": signature,
    }


@pytest.fixture
def client():
    service = KernelService()
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = True
    app.state.execution_kernel_security_policy = CycleApiSecurityPolicy(
        secret=_GATEWAY_SECRET, allowed_gateway_ids=("aix-bff",),
    )
    app.include_router(execution_kernel_router)
    with TestClient(app) as c:
        yield c, service


READ_ONLY = (CyclePermission.READ.value,)
COMMAND_ROLE = (CyclePermission.READ.value, CyclePermission.COMMAND.value)


@pytest.mark.parametrize(
    "path,body",
    [
        ("/aix/execution-kernel/learning", {"summary": "s5 test observation"}),
        ("/aix/execution-kernel/learning/learning_missing/advance", {"to_stage": "PATTERN_CANDIDATE"}),
        ("/aix/execution-kernel/learning/learning_missing/scope", {"scope": "project"}),
        ("/aix/execution-kernel/learning/learning_missing/confidence", {"confidence": "medium"}),
        ("/aix/execution-kernel/learning/learning_missing/reject", {}),
    ],
)
def test_read_only_caller_cannot_mutate_learning_state(client, path: str, body: dict) -> None:
    c, _service = client
    resp = c.post(path, headers=_headers(roles=READ_ONLY, request_id=f"ro-{uuid4().hex[:8]}"), json=body)
    assert resp.status_code == 403, f"{path} allowed a READ-only caller to mutate state: {resp.text}"
    assert resp.json()["detail"]["code"] == "KERNEL_RBAC_FORBIDDEN"


def test_command_caller_can_still_observe_learning(client) -> None:
    c, _service = client
    resp = c.post(
        "/aix/execution-kernel/learning",
        headers=_headers(roles=COMMAND_ROLE, request_id=f"cmd-{uuid4().hex[:8]}"),
        json={"summary": "s5 command-role observation"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


@pytest.mark.parametrize(
    "path,body",
    [
        ("/aix/execution-kernel/semantic/search", {"query": "s5 probe"}),
        ("/aix/execution-kernel/planning/decompose", {"objective": "s5 probe objective"}),
        ("/aix/execution-kernel/planning/autonomy", {"mutation_kind": "add_work_items_same_problem"}),
        ("/aix/execution-kernel/reasoning/query", {"mode": "precedent_search", "question": "s5 probe"}),
    ],
)
def test_read_only_caller_can_still_use_genuinely_read_only_endpoints(client, path: str, body: dict) -> None:
    """These 4 were flagged alongside the real gap but don't mutate
    anything (confirmed by reading their service methods) — a READ-only
    caller must still be able to use them; gating them would be a
    regression, not a fix."""
    c, _service = client
    resp = c.post(path, headers=_headers(roles=READ_ONLY, request_id=f"ro2-{uuid4().hex[:8]}"), json=body)
    assert resp.status_code == 200, f"{path} incorrectly blocked a READ-only caller: {resp.text}"


@pytest.mark.asyncio
async def test_mutation_is_durably_audited_with_actor_identity() -> None:
    """The actor-audit half of this sprint: a real mutating call, through a
    Postgres-backed service, must leave a durable row recording WHO (actor_id)
    did WHAT (action) — the gap the audit found: no ledger event, and only 2
    of the many mutating endpoints, ever recorded an actor identity."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    tenant_id = f"t_audit_{uuid4().hex[:10]}"
    service = PostgresKernelService(database_url=url)
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = True
    app.state.execution_kernel_security_policy = CycleApiSecurityPolicy(
        secret=_GATEWAY_SECRET, allowed_gateway_ids=("aix-bff",),
    )
    app.include_router(execution_kernel_router)

    issued_at = int(time.time())
    request_id = f"audit-probe-{uuid4().hex[:8]}"
    signature = sign_gateway_assertion(
        secret=_GATEWAY_SECRET, gateway_id="aix-bff", tenant_id=tenant_id,
        actor_id="actor-under-audit", roles=COMMAND_ROLE, issued_at_epoch=issued_at, request_id=request_id,
    )
    headers = {
        "Authorization": "Bearer local-token",
        "X-AIX-Gateway-Identity": "aix-bff",
        "X-Tenant-ID": tenant_id,
        "X-AIX-Actor-ID": "actor-under-audit",
        "X-AIX-Roles": ",".join(COMMAND_ROLE),
        "X-AIX-Gateway-Issued-At": str(issued_at),
        "X-Request-ID": request_id,
        "X-AIX-Gateway-Signature": signature,
    }
    with TestClient(app) as c:
        resp = c.post(
            "/aix/execution-kernel/learning",
            headers=headers,
            json={"summary": "audited mutation probe"},
        )
        assert resp.status_code == 200, resp.text

        read_issued_at = int(time.time())
        read_request_id = f"audit-read-{uuid4().hex[:8]}"
        read_signature = sign_gateway_assertion(
            secret=_GATEWAY_SECRET, gateway_id="aix-bff", tenant_id=tenant_id,
            actor_id="actor-under-audit", roles=COMMAND_ROLE, issued_at_epoch=read_issued_at,
            request_id=read_request_id,
        )
        read_headers = {**headers, "X-AIX-Gateway-Issued-At": str(read_issued_at),
                        "X-Request-ID": read_request_id, "X-AIX-Gateway-Signature": read_signature}
        audit = c.get("/aix/execution-kernel/audit/actor-mutations", headers=read_headers)
    assert audit.status_code == 200
    body = audit.json()
    assert body["durable"] is True
    matches = [e for e in body["events"] if e["request_id"] == request_id]
    assert len(matches) == 1, f"expected exactly one audit row for this request, got {matches}"
    assert matches[0]["actor_id"] == "actor-under-audit"
    assert matches[0]["action"] == "POST /aix/execution-kernel/learning"
    assert CyclePermission.COMMAND.value in matches[0]["roles"]
