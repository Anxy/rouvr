from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_core.runtime.objective.api_security import CycleApiSecurityContext, CyclePermission
from backend.routers.objective_cycle_router import (
    get_cycle_api_backend,
    get_cycle_security_context,
    router,
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def command(self, **kwargs):
        self.calls.append({"kind": "command", **kwargs})
        return {"ok": True, "operation": kwargs["operation"], "tenant_id": kwargs["tenant_id"]}

    async def query(self, **kwargs):
        self.calls.append({"kind": "query", **kwargs})
        return {"ok": True, "operation": kwargs["operation"], "tenant_id": kwargs["tenant_id"]}


def _app(*, with_overrides: bool = True) -> tuple[FastAPI, FakeBackend]:
    app = FastAPI()
    app.include_router(router)
    backend = FakeBackend()
    if with_overrides:
        app.dependency_overrides[get_cycle_api_backend] = lambda: backend
        app.dependency_overrides[get_cycle_security_context] = lambda: CycleApiSecurityContext(
            gateway_id="gateway",
            tenant_id="tenant-1",
            actor_id="human-1",
            roles=(CyclePermission.READ.value, CyclePermission.COMMAND.value),
            request_id="request-1",
        )
    return app, backend


def test_expanded_router_exposes_all_22_target_operations() -> None:
    app, _ = _app()
    operations = [
        (method, route.path)
        for route in app.routes
        for method in (route.methods or set())
        if route.path.startswith("/aix/objectives") or route.path.startswith("/aix/cycles")
    ]

    # Was 22, then 24 (reject/block added by another agent), now 25: this session added
    # POST /aix/objectives/discuss (pre-admission governed chat, not part of the
    # original 22-op cycle-command/query surface this test name refers to).
    assert len(operations) == 25
    assert ("POST", "/aix/objectives/discuss") in operations
    paths = {path for _, path in operations}
    assert "/aix/cycles/{cycle_id}/reject" in paths
    assert "/aix/cycles/{cycle_id}/block" in paths


def test_router_fails_closed_without_security_and_durable_backend() -> None:
    app, _ = _app(with_overrides=False)
    response = TestClient(app).get("/aix/cycles")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] in {
        "CYCLE_SECURITY_POLICY_NOT_CONFIGURED",
        "CYCLE_DURABLE_BACKEND_NOT_CONFIGURED",
    }


def test_tenant_scoped_query_dispatches_to_backend() -> None:
    app, backend = _app()
    response = TestClient(app).get("/aix/cycles?limit=25&state=READY")

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "tenant-1"
    assert backend.calls[0]["filters"]["limit"] == 25


def test_command_requires_idempotency_and_if_match() -> None:
    app, _ = _app()
    client = TestClient(app)

    missing_key = client.post("/aix/cycles/cycle-1/cancel", json={"reason": "human"})
    assert missing_key.status_code == 400
    missing_version = client.post(
        "/aix/cycles/cycle-1/cancel",
        headers={"Idempotency-Key": "key-1"},
        json={"reason": "human"},
    )
    assert missing_version.status_code == 428


def test_valid_control_dispatches_actor_tenant_and_version() -> None:
    app, backend = _app()
    response = TestClient(app).post(
        "/aix/cycles/cycle-1/replan",
        headers={"Idempotency-Key": "key-1", "If-Match": 'W/"7"'},
        json={"reason": "verified failure", "replan_input": {"decision": "bounded"}},
    )

    assert response.status_code == 200
    call = backend.calls[0]
    assert call["operation"] == "replan_cycle"
    assert call["tenant_id"] == "tenant-1"
    assert call["actor_id"] == "human-1"
    assert call["expected_version"] == 7


def test_approval_endpoint_validates_reference_not_approval_creation() -> None:
    app, backend = _app()
    client = TestClient(app)
    missing = client.post(
        "/aix/cycles/cycle-1/approval-reference",
        headers={"Idempotency-Key": "key-1", "If-Match": '"1"'},
        json={},
    )
    assert missing.status_code == 422
    response = client.post(
        "/aix/cycles/cycle-1/approval-reference",
        headers={"Idempotency-Key": "key-2", "If-Match": '"1"'},
        json={"approval_reference": "workhub://approvals/approval-1"},
    )

    assert response.status_code == 200
    assert backend.calls[0]["operation"] == "validate_approval_reference"


def test_admit_contract_rejects_unknown_fields() -> None:
    app, _ = _app()
    response = TestClient(app).post(
        "/aix/objectives/admit",
        headers={"Idempotency-Key": "key-1"},
        json={
            "workhub_task_id": "task-1",
            "objective": "bounded objective",
            "task_class": "docs_only_change",
            "definition_of_done": ["tests pass"],
            "repository_id": "aix",
            "auto_accept": True,
        },
    )

    assert response.status_code == 422


class TestDiscussObjective:
    """POST /aix/objectives/discuss -- the governed pre-admission chat this session
    wired up to replace the frontend's local keyword-matching heuristic."""

    def _mock_chat_completion(self, monkeypatch, *, output_text: str):
        import backend.routers.objective_cycle_router as router_module

        class _FakeResult:
            def __init__(self) -> None:
                self.output_text = output_text
                self.resolved_provider = "openrouter"
                self.provider = "openrouter"
                self.model = "some/model"
                self.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        async def _fake_chat_completion(**_kwargs):
            return _FakeResult()

        monkeypatch.setattr(router_module, "chat_completion", _fake_chat_completion)
        # Service path also imports chat_completion directly.
        import backend.infrastructure.model_gateway.intelligence_layer.service as il_service

        monkeypatch.setattr(il_service, "chat_completion", _fake_chat_completion)

    def test_happy_path_returns_parsed_reply_and_task_class(self, monkeypatch) -> None:
        self._mock_chat_completion(
            monkeypatch,
            output_text=(
                '{"reply": "Got it, docs-only.", "normalized_objective": '
                '"Fix the docstrings in auth middleware", "repository_hint": null, '
                '"file_hints": ["docs/**"], "symbol_hints": [], "constraints": [], '
                '"task_class": "docs_only_change", "ambiguous": false}'
            ),
        )
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "Fix the docstrings in auth middleware"}]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["reply"] == "Got it, docs-only."
        assert body["task_class"] == "docs_only_change"
        assert body["proposed_scope"] == ["docs/**"]
        assert body["file_hints"] == ["docs/**"]
        assert body["normalized_objective"] == "Fix the docstrings in auth middleware"
        assert body["ambiguous"] is False

    def test_rbac_forbidden_without_command_permission(self, monkeypatch) -> None:
        self._mock_chat_completion(monkeypatch, output_text='{"reply": "x", "task_class": "docs_only_change"}')
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_cycle_security_context] = lambda: CycleApiSecurityContext(
            gateway_id="gateway",
            tenant_id="tenant-1",
            actor_id="human-1",
            roles=(CyclePermission.READ.value,),  # no COMMAND, no ADMIN
            request_id="request-1",
        )
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 403

    def test_out_of_vocabulary_task_class_is_dropped_not_routing(self, monkeypatch) -> None:
        self._mock_chat_completion(
            monkeypatch,
            output_text=(
                '{"reply": "Sure.", "normalized_objective": "Implement a helper", '
                '"repository_hint": null, "file_hints": [], "symbol_hints": [], '
                '"constraints": [], "task_class": "delete_production_database", '
                '"ambiguous": false}'
            ),
        )
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "Implement a dangerous database wipe helper"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["task_class"] is None
        # Invalid metadata must not force ambiguity — ambiguity is about the goal.
        assert body["ambiguous"] is False
        assert body["planner_invoked"] is True

    def test_filename_free_objective_keeps_empty_hints(self, monkeypatch) -> None:
        self._mock_chat_completion(
            monkeypatch,
            output_text=(
                '{"reply": "Got it. Click Create & run with AIX.", '
                '"normalized_objective": "Add retry handling to payment processing", '
                '"repository_hint": null, "file_hints": [], "symbol_hints": [], '
                '"constraints": [], "task_class": null, "ambiguous": false}'
            ),
        )
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Add retry handling to payment processing",
                    }
                ]
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["file_hints"] == []
        assert body["proposed_scope"] == []
        assert body["task_class"] is None
        assert body["normalized_objective"] == "Add retry handling to payment processing"
        assert body["planner_invoked"] is True

    def test_non_json_planner_reply_falls_back_to_conversation(self, monkeypatch) -> None:
        # Objective utterances invoke planner intake; non-JSON degrades to conversation.
        self._mock_chat_completion(monkeypatch, output_text="Sure, I can help with that!")
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "Fix the login page HTML and CSS"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["planner_invoked"] is False
        assert body["model_role"] == "conversation"
        assert "Sure, I can help" in body["reply"]

    def test_greeting_uses_conversation_not_planner_json(self, monkeypatch) -> None:
        self._mock_chat_completion(monkeypatch, output_text="Hello — how can I help today?")
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hey"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["planner_invoked"] is False
        assert body["model_role"] == "conversation"
        assert body["intent"] == "conversation"
        assert body["task_class"] is None

    def test_empty_reply_fails_closed(self, monkeypatch) -> None:
        # Empty planner reply falls back to conversation; empty conversation still fails closed.
        self._mock_chat_completion(monkeypatch, output_text="")
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "Generate docs for the auth module"}]},
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "MODEL_REPLY_EMPTY"

    def test_integration_chat_error_becomes_502_not_swallowed(self, monkeypatch) -> None:
        import backend.routers.objective_cycle_router as router_module
        from backend.infrastructure.model_gateway.hyeve_integration_client import HyeveIntegrationChatError

        async def _raising_chat_completion(**_kwargs):
            raise HyeveIntegrationChatError("INTEGRATION_UNAVAILABLE", detail="connect timeout")

        monkeypatch.setattr(router_module, "chat_completion", _raising_chat_completion)
        import backend.infrastructure.model_gateway.intelligence_layer.service as il_service

        monkeypatch.setattr(il_service, "chat_completion", _raising_chat_completion)
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "INTEGRATION_UNAVAILABLE"

    def test_missing_authorization_header_rejected(self, monkeypatch) -> None:
        self._mock_chat_completion(monkeypatch, output_text='{"reply": "x", "task_class": "docs_only_change"}')
        app, _ = _app()
        # The dependency override replaces the gateway-assertion security context, but
        # the router's own explicit `authorization = request.headers.get(...)` check is
        # a second, independent gate on the raw request -- no Authorization header here
        # must 401 regardless of the security-context override.
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "AUTHORIZATION_REQUIRED"

    def test_extra_field_in_message_rejected(self) -> None:
        app, _ = _app()
        response = TestClient(app).post(
            "/aix/objectives/discuss",
            json={"messages": [{"role": "user", "content": "hi", "unexpected": True}]},
        )
        assert response.status_code == 422

    def test_empty_messages_rejected(self) -> None:
        app, _ = _app()
        response = TestClient(app).post("/aix/objectives/discuss", json={"messages": []})
        assert response.status_code == 422
