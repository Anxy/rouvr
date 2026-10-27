from __future__ import annotations

from backend.services.model_routing_policy_engine import resolve_model_route


def _policy(model_policy: str, default_provider: str = "slavx", escalation_provider: str = "onlineai") -> dict:
    return {
        "id": "p1",
        "tenant_id": "default",
        "project_id": None,
        "policy_key": "code.medium",
        "task_type": "code_change",
        "risk_level": "medium",
        "model_policy": model_policy,
        "default_provider": default_provider,
        "escalation_provider": escalation_provider,
        "status": "active",
    }


def _binding(provider: str, model_id: str, route_class: str, binding_key: str) -> dict:
    return {
        "id": f"{provider}-{model_id}-{route_class}",
        "tenant_id": "default",
        "binding_key": binding_key,
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "route_class": route_class,
        "is_enabled": True,
    }


def _health(provider: str, model_id: str, status: str) -> dict:
    return {
        "id": f"h-{provider}-{model_id}",
        "tenant_id": "default",
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "health_status": status,
    }


def test_slavx_only_selects_healthy_primary() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="slavx_only",
        project_id=None,
        policies=[_policy("slavx_only", default_provider="slavx")],
        bindings=[_binding("slavx", "deepseek-r1", "primary", "a")],
        health_rows=[_health("slavx", "deepseek-r1", "healthy")],
    )
    assert decision.decision_status == "selected"
    assert decision.selected_provider == "slavx"
    assert decision.selected_model_id == "deepseek-r1"


def test_slavx_only_blocks_when_unhealthy() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="slavx_only",
        project_id=None,
        policies=[_policy("slavx_only", default_provider="slavx")],
        bindings=[_binding("slavx", "deepseek-r1", "primary", "a")],
        health_rows=[_health("slavx", "deepseek-r1", "unhealthy")],
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "NO_HEALTHY_MODEL_FOR_POLICY"


def test_onlineai_only_selects_onlineai() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="onlineai_only",
        project_id=None,
        policies=[_policy("onlineai_only", default_provider="onlineai")],
        bindings=[_binding("onlineai", "gpt-x", "primary", "a")],
        health_rows=[_health("onlineai", "gpt-x", "healthy")],
    )
    assert decision.decision_status == "selected"
    assert decision.selected_provider == "onlineai"


def test_allow_escalate_escalates_without_fallback_route() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "healthy"),
        ],
    )
    assert decision.decision_status == "escalated"
    assert decision.selected_provider == "onlineai"
    assert decision.selected_model_id == "gpt-x"


def test_allow_escalate_escalates_when_default_unhealthy_and_no_fallback() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "healthy"),
        ],
    )
    assert decision.decision_status == "escalated"
    assert decision.selected_provider == "onlineai"
    assert decision.selected_model_id == "gpt-x"


def test_allow_escalate_blocks_when_no_healthy_provider() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "unhealthy"),
        ],
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "ESCALATION_PROVIDER_UNHEALTHY"


def test_deterministic_sorting_uses_binding_key_then_model_id() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="slavx_only",
        project_id=None,
        policies=[_policy("slavx_only", default_provider="slavx")],
        bindings=[
            _binding("slavx", "model-b", "primary", "b"),
            _binding("slavx", "model-a", "primary", "a"),
        ],
        health_rows=[
            _health("slavx", "model-a", "healthy"),
            _health("slavx", "model-b", "healthy"),
        ],
    )
    assert decision.selected_model_id == "model-a"


def test_allow_escalate_blocks_when_retry_threshold_not_reached() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "healthy"),
        ],
        escalation_policies=[
            {
                "tenant_id": "default",
                "policy_key": "esc-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "retry_threshold": 2,
                "escalate_to_provider": "onlineai",
                "status": "active",
            },
        ],
        retry_count=1,
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "RETRY_THRESHOLD_NOT_REACHED"
    assert decision.escalation_threshold == 2


def test_allow_escalate_escalates_when_retry_threshold_reached() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "healthy"),
        ],
        escalation_policies=[
            {
                "tenant_id": "default",
                "policy_key": "esc-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "retry_threshold": 2,
                "escalate_to_provider": "onlineai",
                "status": "active",
            },
        ],
        retry_count=2,
    )
    assert decision.decision_status == "escalated"
    assert decision.escalation_threshold == 2


def test_sg6_healthy_route_selects_primary() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[_binding("slavx", "deepseek-r1", "primary", "a")],
        health_rows=[_health("slavx", "deepseek-r1", "healthy")],
    )
    assert decision.decision_status == "selected"
    assert decision.route_class == "primary"


def test_sg6_unhealthy_primary_does_not_use_fallback_binding() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[_binding("slavx", "deepseek-r1", "primary", "a")],
        health_rows=[_health("slavx", "deepseek-r1", "unhealthy")],
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "NO_ELIGIBLE_HEALTHY_MODEL"


def test_sg6_unhealthy_and_no_fallback_blocks_with_reason() -> None:
    policy = _policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[policy],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-x", "escalation", "c"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "unhealthy"),
            _health("onlineai", "gpt-x", "unhealthy"),
        ],
        escalation_policies=[
            {
                "tenant_id": "default",
                "policy_key": "esc-1",
                "task_type": "code_change",
                "risk_level": "medium",
                "retry_threshold": 0,
                "escalate_to_provider": "onlineai",
                "block_on_provider_unhealthy": True,
                "status": "active",
            },
        ],
        retry_count=0,
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "ESCALATION_PROVIDER_UNHEALTHY"


def test_inactive_policy_is_not_selected() -> None:
    policy = _policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")
    policy["status"] = "inactive"
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[policy],
        bindings=[_binding("slavx", "deepseek-r1", "primary", "a")],
        health_rows=[_health("slavx", "deepseek-r1", "healthy")],
    )
    assert decision.decision_status == "blocked"
    assert decision.decision_reason == "POLICY_NOT_FOUND"


def test_allow_escalate_prefers_phase_provider_when_available() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="onlineai", escalation_provider="slavx")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-4o", "primary", "b"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "healthy"),
            _health("onlineai", "gpt-4o", "healthy"),
        ],
        preferred_provider="slavx",
    )
    assert decision.decision_status == "selected"
    assert decision.selected_provider == "slavx"
    assert decision.selected_model_id == "deepseek-r1"


def test_allow_escalate_force_escalation_bypasses_default_path() -> None:
    decision = resolve_model_route(
        task_type="code_change",
        risk_level="medium",
        model_policy="allow_escalate",
        project_id=None,
        policies=[_policy("allow_escalate", default_provider="slavx", escalation_provider="onlineai")],
        bindings=[
            _binding("slavx", "deepseek-r1", "primary", "a"),
            _binding("onlineai", "gpt-4o", "escalation", "b"),
        ],
        health_rows=[
            _health("slavx", "deepseek-r1", "healthy"),
            _health("onlineai", "gpt-4o", "healthy"),
        ],
        force_escalation=True,
    )
    assert decision.decision_status in {"selected", "escalated"}
    assert decision.selected_provider == "onlineai"
