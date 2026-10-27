"""AIX-EXECUTION-006-S2: Model Enforcement.

A zero-trust audit found that `enforce_executor_model`/`enforce_verifier_model`
were dead code (called only by their own unit test), that the live-dispatch
gate in cycle_adapter.py would reject any real dispatch that actually
supplied model_routing (EXECUTION_ROUTE_REQUIRED, because the raw routing-
policy payload never carried the `enforced`/`route_id` keys the gate checks
for), and that the one real outbound model call in the codebase
(openrouter_codegen.generate_html_css_page) picked its model from a settings
default, ignoring any bound route entirely.

These tests prove, with real code paths (no internal-only assertions on
dead functions), that:
1. A bound route's model actually reaches the outbound request payload.
2. A provider that echoes back a DIFFERENT model than the bound route causes
   a fail-closed EXECUTOR_MODEL_MISMATCH, not a silently-accepted result.
3. prepare_dispatch/live_dispatch now bind a durable route and produce a
   dispatch payload carrying `enforced=True` + `route_id` — the exact shape
   cycle_adapter.py's live-dispatch gate requires — so real routing no
   longer always raises EXECUTION_ROUTE_REQUIRED.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.openrouter_codegen import generate_html_css_page
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


class _FakeSettings:
    OPENROUTER_API_KEY = "test-key"
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
    OPENROUTER_DEFAULT_MODEL = "openai/gpt-5.6-luna"


def _mock_transport(
    captured: dict[str, Any], *, echo_model: str | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["requested_model"] = body.get("model")
        returned_model = echo_model if echo_model is not None else body.get("model")
        return httpx.Response(
            200,
            json={
                "model": returned_model,
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "html": "<html></html>",
                                    "css": "",
                                    "positive_tests": [],
                                    "negative_tests": [],
                                }
                            )
                        }
                    }
                ],
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_model_override_wins_over_settings_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    transport = _mock_transport(captured)
    real_async_client = httpx.AsyncClient

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._client = real_async_client(transport=transport)

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            await self._client.aclose()

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return await self._client.post(*args, **kwargs)

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.openrouter_codegen.httpx.AsyncClient",
        _FakeAsyncClient,
    )

    bundle = await generate_html_css_page(
        objective="Build a status page",
        settings=_FakeSettings(),
        model_override="google/gemini-2.5-pro",
    )
    assert captured["requested_model"] == "google/gemini-2.5-pro"
    assert bundle.usage.model == "google/gemini-2.5-pro"
    assert bundle.usage.ok is True


@pytest.mark.asyncio
async def test_real_execute_fails_closed_when_provider_reports_different_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one real outbound call fails closed if the provider silently
    substitutes a model other than the one the durable route selected —
    enforced against the PROVIDER's own echoed model, not our own request."""

    async def _fake_run_real_execute(**kwargs: Any) -> RealExecuteResult:
        assert kwargs.get("model_override") == "anthropic/claude-sonnet-5"
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace=r"D:\tmp\ws",
            evidence_refs=["file://ws/evidence/execution_report.json"],
            artifacts={},
            token_usage={
                "model": "openai/gpt-3.5-turbo",  # provider substituted a different model
                "provider": "openrouter",
                "ok": True,
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _fake_run_real_execute,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *a, **kw: object(),
    )

    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-1",
            "objective": "Create a status page",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix-frontend",
            # Ground Understanding so execute reaches model-enforcement (empty → NEED_EVIDENCE).
            "allowed_files": ("status.html",),
        },
        idempotency_key="admit-1",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={
            "candidate_sha": "abc1234",
            "budgets": {"max_steps": 3},
            "model_routes": {
                "executor": {"selected_model_id": "anthropic/claude-sonnet-5", "enforced": True, "route_id": "rt_1"}
            },
        },
        idempotency_key="start-1",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/a1"},
        idempotency_key="approve-1",
        expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "EXECUTOR_MODEL_MISMATCH"


@pytest.mark.asyncio
async def test_prepare_dispatch_produces_enforced_route_payload_not_raw_policy_payload() -> None:
    """The exact bug a live audit found: dispatch.model_routes must carry
    enforced=True + route_id (what cycle_adapter.py's live-dispatch gate
    requires), not the raw resolve_kernel_model_routes() payload, which has
    neither key and would make every live dispatch with real routing raise
    EXECUTION_ROUTE_REQUIRED."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_enforce_{suffix}"
    wi_id = f"wi_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.append_and_project(
        tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
        expected_sequence_number=1, event_type="WI_CREATED", command_id=f"c1_{suffix}",
        payload={"status": "PENDING", "title": "enforcement test"},
    )

    routing_context = {
        "task_type": "code_change",
        "risk_level": "medium",
        "model_policy": "allow_escalate",
        "retry_count": 0,
        "policies": [
            {
                "policy_key": "executor-policy", "task_type": "code_change", "risk_level": "medium",
                "model_policy": "allow_escalate", "default_provider": "openrouter",
                "escalation_provider": "openrouter", "max_retry_before_escalate": 0, "status": "active",
            },
        ],
        "bindings": [
            {
                "binding_key": "executor-primary", "provider": "openrouter", "model_id": "openai/gpt-5.1-codex",
                "model_name": "Executor Primary", "route_class": "primary", "is_enabled": True,
            },
        ],
        "health_rows": [
            {"provider": "openrouter", "model_id": "openai/gpt-5.1-codex", "health_status": "healthy"},
        ],
        "escalation_policies": [],
    }
    _capsule, dispatch = await service.prepare_dispatch(
        tenant_id=tenant_id,
        work_item_id=wi_id,
        model_routing=routing_context,
    )
    executor = dispatch.model_routes.get("executor")
    assert executor is not None, "routing was requested but no executor route was produced"
    assert executor.get("enforced") is True, "dispatch payload must carry enforced=True for the live-dispatch gate"
    assert executor.get("route_id"), "dispatch payload must carry a route_id for the live-dispatch gate"

    bound = await service.get_execution_route(tenant_id=tenant_id, work_item_id=wi_id)
    assert bound is not None
    assert bound["route_id"] == executor["route_id"]
