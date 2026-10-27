from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.model_gateway_router import (
    _apply_router_v2_override,
    _evaluate_threshold_violations,
    _extract_phase_router_config,
    _extract_tenant_thresholds,
    router,
)
from backend.services.model_routing_policy_engine import RoutingDecision


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


async def _codeintel_ok(**_kwargs):
    return {"index_jobs": [], "index_jobs_total": 0, "citations": []}, None


def test_extract_tenant_thresholds_prefers_phase_specific_values() -> None:
    metadata_json = {
        "thresholds": {"max_tokens": 1000, "max_estimated_cost": 1.2, "max_latency_ms": 4000},
        "phase_thresholds": {
            "planner": {"max_tokens": 700},
            "executor": {"max_tokens": 500},
            "verifier": {"max_tokens": 300},
        },
        "tenant_thresholds": {
            "default": {
                "max_estimated_cost": 0.6,
                "phase_thresholds": {"executor": {"max_tokens": 250, "max_latency_ms": 1200}},
            }
        },
    }
    thresholds = _extract_tenant_thresholds(metadata_json=metadata_json, tenant_id="default", phase="executor")
    assert thresholds["max_tokens"] == 250
    assert thresholds["max_estimated_cost"] == 0.6
    assert thresholds["max_latency_ms"] == 1200


def test_extract_phase_router_config_exposes_router_profile_fields() -> None:
    metadata_json = {
        "phase_router": {
            "planner": {
                "router_version": "v1",
                "pool_group": "A",
                "privacy_mode": "cloud_ok",
                "routing_mode": "openrouter_auto",
                "provider_sort": "latency",
                "latency_target_ms": 6000,
                "max_price": 0.05,
                "allowed_models": ["openrouter/openai/*", "openrouter/anthropic/*"],
            }
        }
    }
    cfg = _extract_phase_router_config(metadata_json=metadata_json, tenant_id="default", phase="planner")
    assert cfg["router_version"] == "v1"
    assert cfg["pool_group"] == "A"
    assert cfg["privacy_mode"] == "cloud_ok"
    assert cfg["routing_mode"] == "openrouter_auto"
    assert cfg["provider_sort"] == "latency"
    assert cfg["latency_target_ms"] == 6000
    assert cfg["max_price"] == 0.05
    assert cfg["allowed_models"] == ["openrouter/openai/*", "openrouter/anthropic/*"]
    assert isinstance(cfg["recommended_allowed_models"], list)


def test_extract_phase_router_config_has_phase_defaults() -> None:
    planner_cfg = _extract_phase_router_config(metadata_json={}, tenant_id="default", phase="planner")
    executor_cfg = _extract_phase_router_config(metadata_json={}, tenant_id="default", phase="executor")
    verifier_cfg = _extract_phase_router_config(metadata_json={}, tenant_id="default", phase="verifier")
    assert planner_cfg["pool_group"] == "A"
    assert planner_cfg["routing_mode"] == "openrouter_auto"
    assert planner_cfg["recommended_allowed_models"]
    assert executor_cfg["pool_group"] == "B"
    assert executor_cfg["routing_mode"] == "openrouter_auto_exacto"
    assert executor_cfg["recommended_allowed_models"]
    assert verifier_cfg["pool_group"] == "C"
    assert verifier_cfg["routing_mode"] == "openrouter_auto_nitro"
    assert verifier_cfg["recommended_allowed_models"]


def test_threshold_violations_detect_budget_overages() -> None:
    violations = _evaluate_threshold_violations(
        thresholds={"max_tokens": 200, "max_estimated_cost": 0.5, "max_latency_ms": 900},
        tokens_used=300,
        estimated_cost=0.8,
        latency_ms=1200,
    )
    metrics = {item["metric"] for item in violations}
    assert metrics == {"tokens_used", "estimated_cost", "latency_ms"}


def test_resolve_forces_escalation_when_complexity_high(monkeypatch) -> None:
    app = _build_app()

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
                    "metadata_json": {
                        "phase_router": {
                            "executor": {
                                "preferred_provider": "slavx",
                                "complexity_escalation_threshold": 0.6,
                                "circuit_breaker_retry_threshold": 3,
                            }
                        }
                    },
                }
            ]
        }

    async def _list_bindings(**_kwargs):
        return {
            "items": [
                {
                    "binding_key": "a",
                    "provider": "openrouter",
                    "model_id": "z-ai/glm-5",
                    "model_name": "GLM-5",
                    "route_class": "primary",
                    "is_enabled": True,
                },
                {
                    "binding_key": "b",
                    "provider": "openrouter",
                    "model_id": "openrouter/mistralai/codestral-2508",
                    "model_name": "Codestral",
                    "route_class": "primary",
                    "is_enabled": True,
                },
            ]
        }

    async def _list_health(**_kwargs):
        return {
            "items": [
                {"provider": "openrouter", "model_id": "z-ai/glm-5", "health_status": "healthy"},
                {
                    "provider": "openrouter",
                    "model_id": "openrouter/mistralai/codestral-2508",
                    "health_status": "healthy",
                },
            ]
        }

    async def _list_escalation(**_kwargs):
        return {"items": []}

    async def _create_decision(*, data, **_kwargs):
        class _Resp:
            id = "decision-phase-1"
            decision_status = data.decision_status

        return _Resp()

    monkeypatch.setattr("backend.routers.model_gateway_router.validate_tenant_access", _tenant)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_gateway_policies_service.list", _list_policy)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_provider_bindings_service.list", _list_bindings)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_health_status_service.list", _list_health)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_escalation_policies_service.list", _list_escalation)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_gateway_decisions_service.create", _create_decision)
    monkeypatch.setattr(
        "backend.routers.model_gateway_router._get_data_residency_policy",
        lambda: {"default": {"zone": "us", "allowed_providers": ["openrouter"], "allow_external": False}},
    )
    monkeypatch.setattr("backend.routers.model_gateway_router.codeintel_query_client.get_context", _codeintel_ok)

    with TestClient(app) as client:
        response = client.post(
            "/api/aix/model-gateway/resolve",
            json={
                "correlation_id": "corr-phase-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
                "phase": "executor",
                "complexity_score": 0.95,
                "retry_count": 0,
            },
        )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["selected_provider"] == "openrouter"
    assert payload["tenant_thresholds"]["phase_router_force_escalation"] is True


def test_resolve_enforces_allowlisted_models(monkeypatch) -> None:
    app = _build_app()

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
                    "metadata_json": {
                        "phase_router": {
                            "executor": {
                                "allowed_models": ["openrouter/openai/*"],
                                "pool_group": "B",
                                "routing_mode": "openrouter_auto",
                            }
                        }
                    },
                }
            ]
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
            ]
        }

    async def _list_health(**_kwargs):
        return {"items": [{"provider": "slavx", "model_id": "deepseek-r1", "health_status": "healthy"}]}

    async def _list_escalation(**_kwargs):
        return {"items": []}

    async def _create_decision(*, data, **_kwargs):
        class _Resp:
            id = "decision-allowlist-1"
            decision_status = data.decision_status

        return _Resp()

    monkeypatch.setattr("backend.routers.model_gateway_router.validate_tenant_access", _tenant)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_gateway_policies_service.list", _list_policy)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_provider_bindings_service.list", _list_bindings)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_health_status_service.list", _list_health)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_escalation_policies_service.list", _list_escalation)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_gateway_decisions_service.create", _create_decision)
    monkeypatch.setattr("backend.routers.model_gateway_router.codeintel_query_client.get_context", _codeintel_ok)

    with TestClient(app) as client:
        response = client.post(
            "/api/aix/model-gateway/resolve",
            json={
                "correlation_id": "corr-allowlist-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
                "phase": "executor",
            },
        )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["allowlist"]["enforced"] is True
    assert payload["allowlist"]["eligible_bindings"] == 1


def test_resolve_router_v2_overrides_selected_model_from_leaderboard(monkeypatch) -> None:
    app = _build_app()

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
                    "default_provider": "onlineai",
                    "escalation_provider": "slavx",
                    "status": "active",
                    "metadata_json": {
                        "phase_router": {"executor": {"router_version": "v2", "preferred_provider": "onlineai"}}
                    },
                }
            ]
        }

    async def _list_bindings(**_kwargs):
        return {
            "items": [
                {
                    "binding_key": "a",
                    "provider": "openrouter",
                    "model_id": "z-ai/glm-5",
                    "model_name": "GLM-5",
                    "route_class": "primary",
                    "is_enabled": True,
                },
                {
                    "binding_key": "b",
                    "provider": "openrouter",
                    "model_id": "openrouter/mistralai/codestral-2508",
                    "model_name": "Codestral",
                    "route_class": "primary",
                    "is_enabled": True,
                },
            ]
        }

    async def _list_health(**_kwargs):
        return {
            "items": [
                {"provider": "openrouter", "model_id": "z-ai/glm-5", "health_status": "healthy"},
                {
                    "provider": "openrouter",
                    "model_id": "openrouter/mistralai/codestral-2508",
                    "health_status": "healthy",
                },
            ]
        }

    async def _list_escalation(**_kwargs):
        return {"items": []}

    async def _list_decisions(**_kwargs):
        return {
            "items": [
                {
                    "task_type": "code_change",
                    "risk_level": "medium",
                    "model_policy": "allow_escalate",
                    "selected_provider": "openrouter",
                    "selected_model_id": "openrouter/mistralai/codestral-2508",
                    "selected_model_name": "Codestral",
                    "decision_status": "selected",
                    "retry_count": 0,
                    "latency_ms": 120,
                    "estimated_cost": 0.01,
                    "decision_payload_json": {"phase": "executor"},
                }
                for _ in range(4)
            ]
            + [
                {
                    "task_type": "code_change",
                    "risk_level": "medium",
                    "model_policy": "allow_escalate",
                    "selected_provider": "openrouter",
                    "selected_model_id": "z-ai/glm-5",
                    "selected_model_name": "GLM-5",
                    "decision_status": "blocked",
                    "decision_reason": "tool_error",
                    "retry_count": 2,
                    "latency_ms": 900,
                    "estimated_cost": 0.08,
                    "decision_payload_json": {"phase": "executor"},
                }
                for _ in range(4)
            ]
        }

    async def _create_decision(*, data, **_kwargs):
        class _Resp:
            id = "decision-v2-override-1"
            decision_status = data.decision_status

        return _Resp()

    monkeypatch.setattr("backend.routers.model_gateway_router.validate_tenant_access", _tenant)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_gateway_policies_service.list", _list_policy)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_provider_bindings_service.list", _list_bindings)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_model_health_status_service.list", _list_health)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_escalation_policies_service.list", _list_escalation)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_gateway_decisions_service.list", _list_decisions)
    monkeypatch.setattr("backend.routers.model_gateway_router.aix_gateway_decisions_service.create", _create_decision)
    monkeypatch.setattr("backend.routers.model_gateway_router.codeintel_query_client.get_context", _codeintel_ok)

    with TestClient(app) as client:
        response = client.post(
            "/api/aix/model-gateway/resolve",
            json={
                "correlation_id": "corr-v2-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "model_policy": "allow_escalate",
                "phase": "executor",
            },
        )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["selected_model_id"] == "openrouter/mistralai/codestral-2508"
    assert payload["tenant_thresholds"]["router_v2_scoring"]["enabled"] is False
    assert payload["tenant_thresholds"]["router_v2_scoring"]["reason"] == "yaml_persona_pool_preserves_order"
    assert "override" not in payload["tenant_thresholds"]["router_v2_scoring"]


def test_router_v2_override_switches_to_higher_scoring_model() -> None:
    decision = RoutingDecision(
        decision_status="selected",
        selected_provider="onlineai",
        selected_model_id="model-base",
        selected_model_name="Base",
        decision_reason=None,
        route_class="primary",
        policy_key="pk-1",
        retry_count=0,
        escalation_threshold=0,
    )
    overridden, evidence = _apply_router_v2_override(
        decision=decision,
        bindings_for_route=[
            {"provider": "onlineai", "route_class": "primary", "model_id": "model-base"},
            {"provider": "onlineai", "route_class": "primary", "model_id": "model-better"},
        ],
        leaderboard=[
            {"provider": "onlineai", "model_id": "model-base", "model_name": "Base", "score": 0.20, "samples": 8},
            {
                "provider": "onlineai",
                "model_id": "model-better",
                "model_name": "Better",
                "score": 0.70,
                "samples": 8,
                "trend": "promote",
            },
        ],
    )
    assert evidence["override_applied"] is True
    assert overridden.selected_model_id == "model-better"
    assert overridden.decision_reason == "ROUTER_V2_LEADERBOARD_OVERRIDE"
