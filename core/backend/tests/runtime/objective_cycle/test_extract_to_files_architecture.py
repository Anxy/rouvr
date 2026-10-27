"""Regression: architectural god-class extract must not pass as in-file helpers."""

from __future__ import annotations

from backend.aix_core.objective_cycle.objective_coverage import check_objective_coverage
from backend.aix_core.intelligence.repository.php_structural import extract_methods_to_service_files
from backend.aix_core.runtime.executor.required_effects import infer_required_effects, is_architectural_extract_objective


_S6_LIKE = (
    "CronJobController has grown into a god class. Refactor it into a real layered "
    "architecture: extract at least three domain-focused service classes (for example "
    "fee/payment collection, notifications/messaging, and subscription lifecycle), "
    "keep the controller as a thin orchestrator, and preserve public method signatures."
)


def test_architectural_objective_infers_extract_to_files_not_helper() -> None:
    assert is_architectural_extract_objective(_S6_LIKE)
    kinds = {e.kind for e in infer_required_effects(purpose=_S6_LIKE)}
    assert "EXTRACT_TO_FILES" in kinds
    assert "EXTRACT_HELPER" not in kinds
    from backend.aix_core.runtime.executor.required_effects import parse_grouping_keys

    keys = parse_grouping_keys(_S6_LIKE)
    assert len(keys) >= 3
    joined = " ".join(keys).casefold()
    assert "fee" in joined and "notification" in joined and "subscription" in joined
    assert "php -l" not in joined
    assert "constructors" not in joined


def test_standards_cleanup_does_not_infer_extract_to_files() -> None:
    objective = (
        "Refactor D:\\v2.1\\Visions\\CronJobController.php to project standards "
        "without changing behavior."
    )
    purpose = (
        "Bring CronJobController.php into project-standard form through "
        "behavior-preserving organization and import cleanup, while keeping all "
        "existing controller behavior and interfaces intact."
    )
    from backend.aix_core.runtime.executor.required_effects import (
        is_behavior_preserving_cleanup_objective,
        sanitize_required_effects,
    )
    from backend.aix_core.runtime.objective.contracts import RequiredEffectV1

    assert is_architectural_extract_objective(objective, purpose) is False
    assert is_behavior_preserving_cleanup_objective(objective, purpose) is True
    kinds = {e.kind for e in infer_required_effects(purpose=objective)}
    assert "EXTRACT_TO_FILES" not in kinds
    assert "STRIP_UNUSED_IMPORTS" in kinds
    assert "PRESERVE_PUBLIC_INTERFACE" in kinds
    forced = (RequiredEffectV1(kind="EXTRACT_TO_FILES"), RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"))
    sanitized = sanitize_required_effects(forced, objective, purpose)
    assert "EXTRACT_TO_FILES" not in {e.kind for e in sanitized}


def test_python_service_refactor_strips_extract_to_files() -> None:
    """Python delegation refactors use patch lane, not PHP EXTRACT_TO_FILES."""
    from backend.aix_core.runtime.executor.required_effects import effects_for_step
    from backend.aix_core.runtime.objective.contracts import BudgetV1, RequiredEffectV1, StepContractV1

    step = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose=(
            "Controlled refactor of backend/aix_core/runtime/objective/durable_api_backend.py "
            "into a thin orchestration coordinator. Extract verification + terminalization "
            "into TerminalizationService and VerificationService."
        ),
        target_scope=(
            "backend/aix_core/runtime/objective/durable_api_backend.py",
            "backend/aix_core/runtime/objective/services/terminalization_service.py",
        ),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("wire durable_api_backend to delegate",),
        validation_commands=("python -m pytest -q",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        status="PENDING",
    )
    kinds = {e.kind for e in effects_for_step(step)}
    assert "EXTRACT_TO_FILES" not in kinds
    assert "LITERAL_REPLACE" in kinds


def test_helper_only_diff_fails_architectural_coverage() -> None:
    diff = (
        "--- a/CronJobController.php\n+++ b/CronJobController.php\n"
        "@@ -1,3 +1,12 @@\n"
        "+\tprivate function envDatabaseName()\n+\t{\n+\t\treturn env('DB_DATABASE');\n+\t}\n"
        "+\t$mdb = $this->envDatabaseName();\n"
        "+\t$mdb2 = $this->envDatabaseName();\n"
    )
    result = check_objective_coverage(objective=_S6_LIKE, diff_text=diff)
    assert result.satisfied is False
    assert "extract_to_new_types" in result.missing_signals


def test_extract_methods_to_service_files_creates_three_services() -> None:
    src = """<?php
class GodController {
\tpublic function updateFeeDue() { return 1; }
\tpublic function feeSchedule() { return 2; }
\tpublic function pushNotification() { return 3; }
\tpublic function birthdayMessagesNotification() { return 4; }
\tpublic function updateSubscriptionPlan() { return 5; }
\tpublic function smsWebhookUpdate() { return 6; }
}
"""
    updated, created, names = extract_methods_to_service_files(
        src,
        source_path="GodController.php",
        grouping_keys=(
            "fee/payment collection",
            "notifications/messaging",
            "subscription lifecycle",
        ),
        min_groups=3,
    )
    assert len(created) >= 3
    assert len(names) >= 3
    assert "new " in updated
    assert "function updateFeeDue" in updated
    for body in created.values():
        assert "class " in body and "Service" in body
