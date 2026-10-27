from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.policy import (
    POLICY_VERSION,
    PolicyDisposition,
    RiskTier,
    TaskClassPolicy,
)


@pytest.mark.parametrize(
    ("task_class", "risk", "approval"),
    [
        ("docs_only_change", RiskTier.MEDIUM, True),
        ("test_fixture_change", RiskTier.MEDIUM, True),
        ("single_file_helper", RiskTier.MEDIUM, True),
        ("source_edit", RiskTier.MEDIUM, True),
        ("test_generation", RiskTier.MEDIUM, True),
        ("verification_only", RiskTier.LOW, False),
    ],
)
def test_policy_admits_only_restricted_classes(task_class: str, risk: RiskTier, approval: bool) -> None:
    decision = TaskClassPolicy().evaluate(task_class=task_class)

    assert decision.disposition is PolicyDisposition.ADMIT
    assert decision.policy_version == POLICY_VERSION
    assert decision.risk_tier is risk
    assert decision.requires_human_approval is approval


def test_policy_clarifies_missing_task_class() -> None:
    decision = TaskClassPolicy().evaluate(task_class=" ")

    assert decision.disposition is PolicyDisposition.CLARIFY
    assert decision.reason_codes == ("TASK_CLASS_REQUIRED",)


def test_policy_rejects_unsupported_class() -> None:
    decision = TaskClassPolicy().evaluate(task_class="database_migration")

    assert decision.disposition is PolicyDisposition.REJECT
    assert decision.reason_codes == ("UNSUPPORTED_TASK_CLASS",)
    assert decision.risk_tier is RiskTier.PROHIBITED


@pytest.mark.parametrize(
    "effect",
    [
        "auto_accept",
        "auto_close",
        "auto_create_workhub_task",
        "direct_provider_route",
        "production_deploy",
        "workhub_lifecycle_mutation",
    ],
)
def test_policy_rejects_forbidden_authority_effects(effect: str) -> None:
    decision = TaskClassPolicy().evaluate(
        task_class="docs_only_change",
        requested_effects=[effect],
    )

    assert decision.disposition is PolicyDisposition.REJECT
    assert decision.reason_codes == (f"FORBIDDEN_EFFECT:{effect}",)


def test_policy_rejects_production_mutation() -> None:
    decision = TaskClassPolicy().evaluate(
        task_class="single_file_helper",
        environment="production",
    )

    assert decision.disposition is PolicyDisposition.REJECT
    assert decision.reason_codes == ("PRODUCTION_MUTATION_NOT_AUTHORIZED",)


@pytest.mark.parametrize(
    ("task_class", "effect"),
    [
        ("verification_only", "write_scoped_source"),
        ("docs_only_change", "delete_repository"),
        ("test_generation", "write_scoped_docs"),
    ],
)
def test_policy_rejects_unknown_or_cross_class_effects(task_class: str, effect: str) -> None:
    decision = TaskClassPolicy().evaluate(
        task_class=task_class,
        requested_effects=[effect],
    )

    assert decision.disposition is PolicyDisposition.REJECT
    assert decision.risk_tier is RiskTier.PROHIBITED
    assert decision.reason_codes == (f"EFFECT_NOT_ALLOWED:{effect}",)


def test_policy_allows_only_effects_explicitly_scoped_to_task_class() -> None:
    decision = TaskClassPolicy().evaluate(
        task_class="docs_only_change",
        requested_effects=["write_scoped_docs", "run_validation", "attach_evidence"],
    )

    assert decision.disposition is PolicyDisposition.ADMIT
    assert decision.allowed_effects == (
        "attach_evidence",
        "run_validation",
        "write_scoped_docs",
    )


def test_policy_decision_is_deterministic_and_frozen() -> None:
    policy = TaskClassPolicy()
    left = policy.evaluate(
        task_class="verification_only",
        requested_effects=["read_source", "read_source"],
    )
    right = policy.evaluate(
        task_class="verification_only",
        requested_effects=["read_source"],
    )

    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    with pytest.raises(Exception):
        left.disposition = PolicyDisposition.REJECT
