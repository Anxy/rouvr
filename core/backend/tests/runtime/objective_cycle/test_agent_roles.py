from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.agent_roles import (
    FIVE_ROLE_DECISIONS,
    UNIVERSAL_PROHIBITIONS,
    AgentRoleId,
    get_role_decision,
)


def test_exactly_five_unique_roles_are_frozen() -> None:
    assert len(FIVE_ROLE_DECISIONS) == 5
    assert {decision.role_id for decision in FIVE_ROLE_DECISIONS} == set(AgentRoleId)


def test_every_role_inherits_authority_prohibitions() -> None:
    for decision in FIVE_ROLE_DECISIONS:
        assert set(UNIVERSAL_PROHIBITIONS) <= set(decision.must_not)


def test_executor_cannot_self_verify_retry_or_replan() -> None:
    executor = get_role_decision(AgentRoleId.EXECUTOR_CODER)

    assert {"self_verify", "self_retry", "self_replan"} <= set(executor.must_not)


def test_verifier_is_independent_and_cannot_repair() -> None:
    verifier = get_role_decision(AgentRoleId.VERIFIER)

    assert AgentRoleId.EXECUTOR_CODER in verifier.requires_independence_from
    assert "repair_failed_output" in verifier.must_not


def test_recovery_cannot_execute_or_bypass_budget_or_approval() -> None:
    recovery = get_role_decision(AgentRoleId.RECOVERY_PLANNER)

    assert {"execute_repair", "bypass_retry_budget", "bypass_required_approval"} <= set(recovery.must_not)


def test_review_curator_is_not_human_acceptance_authority() -> None:
    review = get_role_decision(AgentRoleId.REVIEW_PACKET_CURATOR)

    assert {"accept_workhub_task", "reject_workhub_task", "manufacture_human_approval"} <= set(review.must_not)
    assert "recommend_disposition" in review.may


def test_planner_cannot_execute_or_grant_approval() -> None:
    planner = get_role_decision(AgentRoleId.PLANNER)

    assert {"execute_commands", "grant_approval", "widen_objective_scope"} <= set(planner.must_not)


def test_unknown_role_fails_closed() -> None:
    with pytest.raises(ValueError):
        get_role_decision("supervisor")


def test_role_decisions_are_immutable() -> None:
    with pytest.raises(Exception):
        FIVE_ROLE_DECISIONS[0].autonomy_cap = "L4"
