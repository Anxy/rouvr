from __future__ import annotations

from typing import Any

from backend.legacy.orphaned_services.outcome_policy_registry_service import (
    build_policy_recovery_plan,
    resolve_recovery_policy,
)

SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "diagnostic_grounding_miss",
        "kwargs": {
            "code": "TARGET_LOGIC_NOT_CONFIRMED",
            "workflow_stage": "planning",
            "task_intent": "code_change_required",
            "tenant_id": "default",
            "repo_profile_id": "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT",
            "provider": "slavx",
            "model": "openai/gpt-4.1-mini",
        },
        "expected": {
            "policy_action": "replan",
            "failure_family": "grounding_failed",
            "retry_budget_key": "",
            "matched_policy": "default",
        },
        "plan_code": "TARGET_LOGIC_NOT_CONFIRMED",
    },
    {
        "name": "syntax_retry_quality_gate",
        "kwargs": {
            "code": "CANDIDATE_SYNTAX_INVALID",
            "workflow_stage": "quality_gate",
            "task_intent": "code_change_required",
            "tenant_id": "default",
            "repo_profile_id": "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT",
            "provider": "slavx",
            "model": "openai/gpt-4.1-mini",
        },
        "expected": {
            "policy_action": "retry",
            "failure_family": "parse_error",
            "retry_budget_key": "parse_failure",
            "matched_policy": "default",
        },
        "plan_code": "CANDIDATE_SYNTAX_INVALID",
    },
    {
        "name": "behavioural_override_reject",
        "kwargs": {
            "code": "TEST_BEHAVIOUR_REGRESSION",
            "workflow_stage": "quality_gate",
            "task_intent": "code_change_required",
            "tenant_id": "default",
            "repo_profile_id": "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT",
            "provider": "slavx",
            "model": "openai/gpt-4.1-mini",
            "tool_class": "verifier",
            "quality_message": "behavioural test regression detected",
        },
        "expected": {
            "policy_action": "verifier_reject",
            "failure_family": "test_behavioural",
            "retry_budget_key": "test_behavioural_failure",
            "matched_policy": "behavioural_tests_do_not_inline_retry",
        },
        "plan_code": "FEATURE_SLICE_INCOMPLETE",
    },
    {
        "name": "noop_mutation_replan",
        "kwargs": {
            "code": "NOOP_CODE_CHANGE",
            "workflow_stage": "quality_gate",
            "task_intent": "code_change_required",
            "tenant_id": "default",
            "repo_profile_id": "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT",
            "provider": "slavx",
            "model": "openai/gpt-4.1-mini",
        },
        "expected": {
            "policy_action": "retry",
            "failure_family": "mutation_missing",
            "retry_budget_key": "mutation_missing",
            "matched_policy": "default",
        },
        "plan_code": "NOOP_CODE_CHANGE",
    },
]


def test_outcome_policy_replay_matrix_is_deterministic() -> None:
    for scenario in SCENARIOS:
        first = resolve_recovery_policy(**scenario["kwargs"])
        second = resolve_recovery_policy(**scenario["kwargs"])

        assert first == second, scenario["name"]
        assert first["policy_action"] == scenario["expected"]["policy_action"], scenario["name"]
        assert first["failure_family"] == scenario["expected"]["failure_family"], scenario["name"]
        assert first["retry_budget_key"] == scenario["expected"]["retry_budget_key"], scenario["name"]
        assert first["matched_policy"] == scenario["expected"]["matched_policy"], scenario["name"]


def test_outcome_policy_replay_matrix_builds_recovery_plans_for_representative_paths() -> None:
    for scenario in SCENARIOS:
        plan = build_policy_recovery_plan(
            code=scenario["plan_code"],
            targets=["backend/indexing/runner.py"],
            summary=f"scenario={scenario['name']}",
        )

        assert plan is not None, scenario["name"]
        assert len(plan) >= 1, scenario["name"]
        assert plan[0]["targets"] == ["backend/indexing/runner.py"], scenario["name"]
