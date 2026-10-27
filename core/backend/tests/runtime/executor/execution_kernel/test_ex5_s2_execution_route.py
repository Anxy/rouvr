"""EX5-S2: Model Execution Integrity — route binding, enforcement, switch, audit."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.aix_core.runtime.executor.execution_kernel_adapter.bootstrap import BootstrapSpec, BootstrapWorkItem
from backend.aix_core.runtime.executor.execution_route import (
    ModelExecutionContext,
    RecordingModelExecutor,
    enforce_executor_model,
    enforce_verifier_model,
)
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService
from backend.routers.execution_kernel_router import router as execution_kernel_router

_ROUTES = {
    "executor": {"selected_provider": "openrouter", "selected_model_id": "openai/gpt-5.1-codex"},
    "verifier": {"selected_provider": "openrouter", "selected_model_id": "anthropic/claude-sonnet-5"},
}


def _routing_context() -> dict:
    """A realistic gateway routing_context — reuses the same shape
    test_release2_p3_p4.py's _routing_context() proves resolve_kernel_model_routes
    actually resolves, not a hand-waved flat routes dict."""
    return {
        "task_type": "code_change",
        "verifier_task_type": "verification",
        "risk_level": "medium",
        "model_policy": "allow_escalate",
        "retry_count": 0,
        "policies": [
            {
                "policy_key": "executor-policy", "task_type": "code_change", "risk_level": "medium",
                "model_policy": "allow_escalate", "default_provider": "openrouter",
                "escalation_provider": "openrouter", "max_retry_before_escalate": 0, "status": "active",
            },
            {
                "policy_key": "verifier-policy", "task_type": "verification", "risk_level": "medium",
                "model_policy": "allow_escalate", "default_provider": "openrouter",
                "escalation_provider": "openrouter", "max_retry_before_escalate": 0, "status": "active",
            },
        ],
        "bindings": [
            {
                "binding_key": "executor-primary", "provider": "openrouter", "model_id": "openai/gpt-5.1-codex",
                "model_name": "Executor Primary", "route_class": "primary", "is_enabled": True,
            },
            {
                "binding_key": "verifier-primary", "provider": "openrouter", "model_id": "anthropic/claude-sonnet-5",
                "model_name": "Verifier Primary", "route_class": "primary", "is_enabled": True,
            },
        ],
        "health_rows": [
            {"provider": "openrouter", "model_id": "openai/gpt-5.1-codex", "health_status": "healthy"},
            {"provider": "openrouter", "model_id": "anthropic/claude-sonnet-5", "health_status": "healthy"},
        ],
    }


@pytest.mark.asyncio
async def test_prepare_dispatch_durably_binds_the_resolved_route() -> None:
    """A dispatch that resolves model_routing must leave a durable, queryable
    route binding behind — not just hand routes to the caller and forget them."""
    svc = KernelService()
    for body in (
        ("project", "proj_1", 1, "PROJECT_CREATED", "c1", {"status": "ACTIVE"}),
        ("capability", "cap_1", 1, "CAPABILITY_CREATED", "c2", {"status": "ACTIVE", "parent_id": "proj_1"}),
        ("problem", "pr_1", 1, "PROBLEM_CREATED", "c3", {"status": "EXECUTING", "parent_id": "cap_1"}),
        ("work_item", "wi_1", 1, "WI_CREATED", "c4", {"status": "PENDING", "parent_id": "pr_1"}),
    ):
        await svc.append_and_project(
            tenant_id="t1", aggregate_type=body[0], aggregate_id=body[1],
            expected_sequence_number=body[2], event_type=body[3], command_id=body[4], payload=body[5],
        )

    assert await svc.get_execution_route(tenant_id="t1", work_item_id="wi_1") is None

    _, dispatch = await svc.prepare_dispatch(
        tenant_id="t1", work_item_id="wi_1",
        model_routing=_routing_context(), include_verifier_route=True,
    )

    route = await svc.get_execution_route(tenant_id="t1", work_item_id="wi_1")
    assert route is not None
    assert route["status"] == "ACTIVE"
    assert route["executor"]["provider"] == "openrouter"
    assert route["executor"]["model_id"] == "openai/gpt-5.1-codex"

    audit = await svc.list_execution_route_audit(tenant_id="t1", work_item_id="wi_1")
    assert any(e["event_type"] == "ROUTE_BOUND" for e in audit)


@pytest.mark.asyncio
async def test_switch_requires_reason_and_supersedes_not_drops_previous_route() -> None:
    svc = KernelService()
    await svc.append_and_project(
        tenant_id="t1", aggregate_type="work_item", aggregate_id="wi_1",
        expected_sequence_number=1, event_type="WI_CREATED", command_id="c1", payload={"status": "PENDING"},
    )
    svc._bind_execution_route(tenant_id="t1", work_item_id="wi_1", model_routes=_ROUTES)
    first_route = await svc.get_execution_route(tenant_id="t1", work_item_id="wi_1")

    with pytest.raises(ExecutionKernelRepositoryError, match="reason is required"):
        await svc.switch_execution_route(
            tenant_id="t1", work_item_id="wi_1",
            model_routes={"executor": {"selected_provider": "openrouter", "selected_model_id": "google/gemini-3-pro"}},
            reason="",
        )

    switched = await svc.switch_execution_route(
        tenant_id="t1", work_item_id="wi_1",
        model_routes={"executor": {"selected_provider": "openrouter", "selected_model_id": "google/gemini-3-pro"}},
        reason="primary provider unhealthy",
        as_fallback=True,
    )
    assert switched["status"] == "FALLBACK"
    assert switched["switch_reason"] == "primary provider unhealthy"

    history = await svc.list_execution_route_history(tenant_id="t1", work_item_id="wi_1")
    statuses = {h["route_id"]: h["status"] for h in history}
    assert statuses[first_route["route_id"]] == "SUPERSEDED", (
        "the original route must be retained as SUPERSEDED, never silently dropped"
    )

    audit = await svc.list_execution_route_audit(tenant_id="t1", work_item_id="wi_1")
    assert any(e["event_type"] == "ROUTE_FALLBACK" for e in audit)
    assert any(e["event_type"] == "ROUTE_SUPERSEDED" for e in audit)


def test_enforce_executor_model_fails_closed_on_mismatch() -> None:
    """The whole point of EX5-S2: a bound route is not advisory. An executor
    that runs on a different model than the one selected must be rejected,
    not silently accepted."""
    from backend.aix_core.runtime.executor.execution_route import contract_from_model_routes

    contract = contract_from_model_routes(tenant_id="t1", work_item_id="wi_1", model_routes=_ROUTES)
    context = ModelExecutionContext(route=contract)

    with pytest.raises(ExecutionKernelRepositoryError, match="EXECUTOR_MODEL_MISMATCH"):
        enforce_executor_model(context, provider="openrouter", model_id="some/wrong-model")

    enforce_executor_model(context, provider="openrouter", model_id="openai/gpt-5.1-codex")
    assert context.actual_executor_model_id == "openai/gpt-5.1-codex"

    with pytest.raises(ExecutionKernelRepositoryError, match="VERIFIER_MODEL_MISMATCH"):
        enforce_verifier_model(context, provider="openrouter", model_id="wrong/verifier")

    enforce_verifier_model(context, provider="openrouter", model_id="anthropic/claude-sonnet-5")


@pytest.mark.asyncio
async def test_recording_model_executor_refuses_to_run_off_route() -> None:
    from backend.aix_core.runtime.executor.execution_route import contract_from_model_routes

    contract = contract_from_model_routes(tenant_id="t1", work_item_id="wi_1", model_routes=_ROUTES)
    context = ModelExecutionContext(route=contract)

    wrong_executor = RecordingModelExecutor(provider="openrouter", model_id="not/the-bound-model")
    with pytest.raises(ExecutionKernelRepositoryError, match="EXECUTOR_MODEL_MISMATCH"):
        await wrong_executor.execute(context=context, payload={})
    assert wrong_executor.calls == []

    right_executor = RecordingModelExecutor(provider="openrouter", model_id="openai/gpt-5.1-codex")
    result = await right_executor.execute(context=context, payload={"task": "t"})
    assert result["ok"] is True
    assert len(right_executor.calls) == 1


@pytest.mark.asyncio
async def test_ex5_s2_execution_route_http_surface() -> None:
    service = KernelService()
    await service.bootstrap_graph(
        tenant_id="t1",
        idempotency_key="s2-http-boot",
        spec=BootstrapSpec(
            vision_summary="V", requirements_summary="R", capability_name="C", charter_summary="Ch",
            work_items=[BootstrapWorkItem(title="WI", work_item_id="wi_h")],
            project_id="proj_h", capability_id="cap_h", problem_id="pr_h",
        ),
    )
    app = FastAPI()
    app.state.execution_kernel_service = service
    app.state.execution_kernel_require_gateway = False
    app.include_router(execution_kernel_router)
    headers = {"X-Tenant-ID": "t1"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get("/aix/execution-kernel/execution-routes/wi_h", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["route"] is None

        service._bind_execution_route(tenant_id="t1", work_item_id="wi_h", model_routes=_ROUTES)

        got = await client.get("/aix/execution-kernel/execution-routes/wi_h", headers=headers)
        assert got.status_code == 200
        assert got.json()["route"]["executor"]["model_id"] == "openai/gpt-5.1-codex"

        switched = await client.post(
            "/aix/execution-kernel/execution-routes/wi_h/switch",
            headers=headers,
            json={
                "model_routes": {"executor": {"selected_provider": "openrouter", "selected_model_id": "google/gemini-3-pro"}},
                "reason": "operator override",
            },
        )
        assert switched.status_code == 200
        assert switched.json()["route"]["switch_reason"] == "operator override"

        history = await client.get("/aix/execution-kernel/execution-routes/wi_h/history", headers=headers)
        assert history.status_code == 200
        assert history.json()["count"] >= 2

        audit = await client.get("/aix/execution-kernel/execution-routes/wi_h/audit", headers=headers)
        assert audit.status_code == 200
        assert audit.json()["count"] >= 1
