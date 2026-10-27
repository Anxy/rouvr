from __future__ import annotations

import pytest

from backend.main import app


IMPLEMENTED_MVP_PRODUCT_API_ROUTES = {
    "/aix/tasks/admit": "post",
    "/aix/tasks/execute": "post",
    "/aix/tasks/{id}/status": "get",
    "/aix/tasks/{id}/evidence": "get",
    "/aix/tasks/{id}/review-decision": "post",
    "/aix/replay/{evidence_id}": "post",
}


def test_main_app_openapi_exposes_aix_orchestrator_v2_routes() -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})

    assert "/api/aix/orchestrator-v2/runs" in paths
    assert "post" in paths["/api/aix/orchestrator-v2/runs"]
    assert "/api/aix/orchestrator-v2/runs/{run_id}" in paths
    assert "get" in paths["/api/aix/orchestrator-v2/runs/{run_id}"]
    assert "/api/aix/orchestrator-v2/tasks/{task_id}/runs/latest" in paths
    assert "get" in paths["/api/aix/orchestrator-v2/tasks/{task_id}/runs/latest"]


def test_main_app_openapi_does_not_expose_alternate_executor_entrypoints() -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})

    assert "/api/aix/devexecutor/runs" not in paths
    assert "/api/aix/runtime-pool/start" not in paths


def test_main_app_openapi_exposes_operator_run_routes() -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})

    assert "/api/aix/runs" in paths
    assert "get" in paths["/api/aix/runs"]
    assert "/api/aix/runs/{run_id}" in paths
    assert "get" in paths["/api/aix/runs/{run_id}"]
    assert "/api/aix/runs/{run_id}/evidence" in paths
    assert "get" in paths["/api/aix/runs/{run_id}/evidence"]
    assert "/api/aix/runs/{run_id}/replay-comparison" in paths
    assert "get" in paths["/api/aix/runs/{run_id}/replay-comparison"]


@pytest.mark.parametrize(("path", "method"), sorted(IMPLEMENTED_MVP_PRODUCT_API_ROUTES.items()))
def test_main_app_openapi_exposes_mvp_product_api_contract(path: str, method: str) -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})

    assert path in paths
    assert method in paths[path]
