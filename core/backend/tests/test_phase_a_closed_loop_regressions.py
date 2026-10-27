from __future__ import annotations

import ast
from pathlib import Path

from backend.legacy.orphaned_services.outcome_policy_registry_service import resolve_recovery_policy

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_cases() -> list[dict]:
    source = (FIXTURES_DIR / "phase_a_closed_loop_regressions.py").read_text(encoding="utf-8")
    module = ast.parse(source, filename="phase_a_closed_loop_regressions.py", mode="exec")
    assign = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PAYLOAD" for target in node.targets)
    )
    payload = ast.literal_eval(assign.value)
    return list(payload["cases"])


def test_phase_a_closed_loop_regressions_are_registered_with_retry_semantics() -> None:
    cases = _load_cases()
    assert len(cases) >= 7
    for case in cases:
        policy = resolve_recovery_policy(
            code=case["code"],
            workflow_stage=case["workflow_stage"],
            task_intent=case["task_intent"],
        )
        assert policy["policy_action"] == case["expected_policy_action"]
        assert policy["failure_family"] == case["expected_failure_family"]
        assert policy["retry_budget_key"] == case["expected_retry_budget_key"]


def test_phase_a_closed_loop_regressions_do_not_fall_back_to_unknown() -> None:
    cases = _load_cases()
    for case in cases:
        policy = resolve_recovery_policy(
            code=case["code"],
            workflow_stage=case["workflow_stage"],
            task_intent=case["task_intent"],
        )
        assert policy["outcome_class"] != "unknown"
        assert policy["tool_class"] != "unknown"
