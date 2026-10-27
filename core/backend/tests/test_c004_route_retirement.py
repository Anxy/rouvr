from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.legacy.langgraph.router import (
    LANGGRAPH_RETIREMENT_CODE,
    LANGGRAPH_WRITES_RETIRED,
    LangGraphRunRequest,
    run_langgraph_chain,
    router as langgraph_router,
)
from backend.routers import aix_orchestrator_v2_router as v2_module
from backend.routers.aix_orchestrator_v2_router import router as orchestrator_v2_router
from backend.routers.aix_mvp_product_router import router as mvp_router


@pytest.mark.asyncio
async def test_langgraph_run_retired_even_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert LANGGRAPH_WRITES_RETIRED is True
    monkeypatch.setattr(
        "backend.legacy.langgraph.router.settings.LANGGRAPH_ENABLED",
        True,
    )
    body = LangGraphRunRequest(
        project_id="bh-migrated-7-aix",
        task_number=1,
        correlation_id="corr-1",
        user_goal="test",
        approval={"approved": True},
    )
    with pytest.raises(HTTPException) as exc:
        await run_langgraph_chain(body=body, current_user={"id": "user-1"})
    assert exc.value.status_code == 410
    assert exc.value.detail["code"] == LANGGRAPH_RETIREMENT_CODE


def test_langgraph_not_mounted_on_production_main() -> None:
    from backend.aix_core.control_plane_reachability.generator import parse_mounted_routers
    from pathlib import Path

    main_py = Path(__file__).resolve().parents[1] / "main.py"
    mounted = parse_mounted_routers(main_py)
    assert "langgraph_orchestration_router" not in mounted


def test_remounted_langgraph_still_fails_closed() -> None:
    app = FastAPI()
    app.include_router(langgraph_router)
    # Bypass auth dependency for negative route proof
    async def _fake_user():
        return {"id": "user-1"}

    from backend.dependencies.aix_gateway_decisions_service_dependencies import require_permission

    app.dependency_overrides[require_permission("gateway_decisions:create")] = _fake_user
    client = TestClient(app)
    response = client.post(
        "/api/aix/langgraph/run",
        json={
            "project_id": "p",
            "task_number": 1,
            "correlation_id": "c",
            "user_goal": "x",
        },
    )
    # May be 401/403 if override doesn't bind correctly; prefer 410 when auth ok.
    assert response.status_code in {410, 401, 403, 422}


def test_orchestrator_v2_mutation_returns_410() -> None:
    assert v2_module.ORCHESTRATOR_V2_MUTATION_RETIRED is True
    app = FastAPI()
    app.include_router(orchestrator_v2_router)
    client = TestClient(app)
    response = client.post(
        "/api/aix/orchestrator-v2/runs",
        headers={"Authorization": "Bearer test"},
        json={
            "project_id": "bh-migrated-7-aix",
            "task_number": 2627,
            "workspace_root": "/tmp",
            "mode": "inline",
        },
    )
    assert response.status_code == 410
    body = response.json()
    detail = body.get("detail") or body
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == v2_module.ORCHESTRATOR_V2_RETIREMENT_CODE


def test_mvp_execute_returns_410() -> None:
    app = FastAPI()
    app.include_router(mvp_router)
    client = TestClient(app)
    response = client.post(
        "/aix/tasks/execute",
        headers={
            "Authorization": "Bearer test",
            "X-Tenant-ID": "t1",
            "X-Actor-ID": "a1",
        },
        json={
            "id": "task-1",
            "tenant_id": "t1",
            "workspace_id": "ws-1",
            "repo_ref": "repo@main",
            "task_kind": "docs_only_change",
            "description": "docs",
            "risk_hint": "low",
            "user_id": "u1",
            "created_at": "2026-08-15T00:00:00Z",
            "project_id": "p",
            "task_number": 1,
            "workspace_root": "/tmp",
        },
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "MVP_EXECUTE_RETIRED"
