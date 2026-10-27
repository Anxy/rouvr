from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.model_gateway_router import router


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/aix/model-gateway/"):
            continue
        deps = getattr(route, "dependant", None)
        if not deps or not deps.dependencies:
            continue
        auth_dep = deps.dependencies[0].call
        app.dependency_overrides[auth_dep] = lambda: {"id": "user-1", "tenant_id": "default"}
    return app


def _patch_resolve_dependencies(monkeypatch) -> None:
    async def _tenant(_current_user):
        return "default"

    async def _list_policy(**_kwargs):
        return {
            "items": [
                {
                    "policy_key": "p1",
                    "task_type": "code_change",
                    "risk_level": "medium",
                    "model_policy": "allow_escalate",
                    "default_provider": "slavx",
                    "escalation_provider": "onlineai",
                    "status": "active",
                },
            ],
        }

    async def _list_bindings(**_kwargs):
        return {
            "items": [
                {
                    "binding_key": "a",
                    "provider": "slavx",
                    "model_id": "deepseek-r1",
                    "model_name": "DeepSeek",
                    "route_class": "primary",
                    "is_enabled": True,
                },
            ],
        }

    async def _list_health(**_kwargs):
        return {"items": [{"provider": "slavx", "model_id": "deepseek-r1", "health_status": "healthy"}]}

    async def _list_escalation(**_kwargs):
        return {"items": []}

    async def _create_decision(*, data, **_kwargs):
        class _Resp:
            id = "decision-shadow-1"
            decision_status = data.decision_status

        return _Resp()

    async def _codeintel_ok(**_kwargs):
        return {"index_jobs": [], "index_jobs_total": 0, "citations": []}, None

    monkeypatch.setattr("backend.routers.model_gateway_router.validate_tenant_access", _tenant)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_gateway_policies_service.list", _list_policy)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_provider_bindings_service.list", _list_bindings)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_health_status_service.list", _list_health)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_escalation_policies_service.list", _list_escalation)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_gateway_decisions_service.create", _create_decision)
    monkeypatch.setattr("backend.routers.model_gateway_router.codeintel_query_client.get_context", _codeintel_ok)
    monkeypatch.setattr(
        "backend.routers.model_gateway_router._get_data_residency_policy",
        lambda: {"default": {"zone": "us", "allowed_providers": ["slavx", "onlineai"], "allow_external": True}},
    )


def test_resolve_enqueues_ayrpack_shadow_write_when_enabled(monkeypatch) -> None:
    app = _build_app()
    _patch_resolve_dependencies(monkeypatch)

    captured: dict[str, object] = {}

    def _enqueue(**kwargs):
        captured.update(kwargs)
        return True, None

    monkeypatch.setattr("backend.routers.model_gateway_router.settings.AYRPACK_SHADOW_WRITE_ENABLED", True)
    monkeypatch.setattr("backend.routers.model_gateway_router._enqueue_ayrpack_shadow_write", _enqueue)

    with TestClient(app) as client:
        response = client.post(
            "/api/aix/model-gateway/resolve",
            json={
                "project_id": "ayrpack",
                "correlation_id": "corr-shadow-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
            },
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["decision_record_id"] == "decision-shadow-1"
    assert payload["ayrpack_shadow_write_enqueued"] is True
    assert payload["ayrpack_shadow_write_reason"] is None
    assert payload["ayrpack_rollout_flags"] == {
        "shadow_write_enabled": True,
        "shadow_write_role": "debug_forensic_mirror",
        "shadow_write_required_for_control_plane": False,
    }
    assert payload["ayrpack_rollout_telemetry"]["packet_write"]["shadow_enqueued"] is True
    assert payload["ayrpack_rollout_telemetry"]["packet_write"]["role"] == "debug_forensic_mirror"
    assert payload["ayrpack_rollout_telemetry"]["packet_write"]["required_for_control_plane"] is False
    request = captured["request"]
    assert getattr(request, "correlation_id", None) == "corr-shadow-1"
    assert captured["tenant_id"] == "default"


def test_resolve_continues_when_ayrpack_shadow_write_is_skipped(monkeypatch) -> None:
    app = _build_app()
    _patch_resolve_dependencies(monkeypatch)

    monkeypatch.setattr("backend.routers.model_gateway_router.settings.AYRPACK_SHADOW_WRITE_ENABLED", False)

    with TestClient(app) as client:
        response = client.post(
            "/api/aix/model-gateway/resolve",
            json={
                "project_id": "ayrpack",
                "correlation_id": "corr-shadow-2",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
            },
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["decision_record_id"] == "decision-shadow-1"
    assert payload["ayrpack_shadow_write_enqueued"] is False
    assert payload["ayrpack_shadow_write_reason"] == "AYRPACK_SHADOW_WRITE_DISABLED"
    assert payload["ayrpack_rollout_flags"] == {
        "shadow_write_enabled": False,
        "shadow_write_role": "debug_forensic_mirror",
        "shadow_write_required_for_control_plane": False,
    }
    assert payload["ayrpack_rollout_telemetry"]["packet_write"]["coverage"] == 0.0
    assert payload["ayrpack_rollout_telemetry"]["packet_write"]["authority"] == "non_canonical"
