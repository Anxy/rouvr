"""Tests for F7-P2 AgentBinding & Governed Model Route Parity."""

from __future__ import annotations

from backend.aix_core.framework.agent.binding import AgentBinding
from backend.aix_core.framework.agent.models import Role
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter


def test_agent_binding_planner_parity():
    router = GovernedProviderRouter()
    direct_decision = router.resolve_role("planner", model_ref="claude-3-7-sonnet")

    binding = AgentBinding.for_role(
        Role.PLANNER,
        model_ref="claude-3-7-sonnet",
        router=router,
    )

    assert binding.agent.role == Role.PLANNER
    assert binding.route_decision.disposition == "ALLOW"
    assert direct_decision.route is not None
    assert binding.route_decision.route is not None
    assert binding.route_decision.route.provider_ref == direct_decision.route.provider_ref
    assert binding.route_decision.route.route_ref == direct_decision.route.route_ref


def test_agent_binding_executor_coder_parity():
    router = GovernedProviderRouter()
    direct_decision = router.resolve_role("executor", model_ref="claude-3-7-sonnet")

    binding = AgentBinding.for_role(
        Role.EXECUTOR_CODER,
        model_ref="claude-3-7-sonnet",
        router=router,
    )

    assert binding.agent.role == Role.EXECUTOR_CODER
    assert binding.route_decision.disposition == "ALLOW"
    assert binding.route_decision.route is not None
    assert direct_decision.route is not None
    assert binding.route_decision.route.provider_ref == direct_decision.route.provider_ref


def test_agent_binding_verifier_parity():
    router = GovernedProviderRouter()
    direct_decision = router.resolve_role("verifier", model_ref="gpt-4o")

    binding = AgentBinding.for_role(
        Role.VERIFIER,
        model_ref="gpt-4o",
        router=router,
    )

    assert binding.agent.role == Role.VERIFIER
    assert binding.route_decision.disposition == "ALLOW"
    assert binding.route_decision.route is not None
    assert direct_decision.route is not None
    assert binding.route_decision.route.provider_ref == direct_decision.route.provider_ref


def test_agent_binding_fails_on_empty_model_ref_when_no_registry():
    router = GovernedProviderRouter()
    # When model ref is empty string, router returns DENY
    decision = router.resolve(role="planner", model_ref="")
    assert decision.disposition == "DENY"
