from __future__ import annotations

import pytest

from backend.aix_core.runtime.planner.experimental.decomposer import (
    AcceptanceCriterion,
    BoundedDecomposer,
    CandidateStep,
    DecompositionDisposition,
    ObjectiveBrief,
)

HASH = "a" * 64


def _brief(**changes) -> ObjectiveBrief:
    payload = {
        "objective_id": "objective-1",
        "tenant_id": "tenant-1",
        "task_class": "docs_only_change",
        "statement": "Update bounded documentation.",
        "repository_id": "aix",
        "allowed_files": ("docs/current/a.md",),
        "acceptance_criteria": (
            AcceptanceCriterion(criterion_id="ac-1", description="Document the governed boundary."),
            AcceptanceCriterion(criterion_id="ac-2", description="Run the focused tests."),
        ),
        "context_capsule_sha256": HASH,
    }
    payload.update(changes)
    return ObjectiveBrief(**payload)


def _step(**changes) -> CandidateStep:
    payload = {
        "step_id": "step-1",
        "purpose": "Implement the bounded change.",
        "repository_id": "aix",
        "target_files": ("docs/current/a.md",),
        "acceptance_criterion_ids": ("ac-1", "ac-2"),
    }
    payload.update(changes)
    return CandidateStep(**payload)


def test_decomposer_builds_bounded_default_steps() -> None:
    result = BoundedDecomposer().decompose(_brief())

    assert result.disposition is DecompositionDisposition.CANDIDATE
    assert len(result.steps) == 2
    assert {item for step in result.steps for item in step.acceptance_criterion_ids} == {"ac-1", "ac-2"}
    assert result.decomposition_sha256


def test_decomposer_is_deterministic() -> None:
    decomposer = BoundedDecomposer()

    assert decomposer.decompose(_brief()).model_dump(mode="json") == decomposer.decompose(_brief()).model_dump(
        mode="json"
    )


def test_decomposer_rejects_unsupported_task_class() -> None:
    result = BoundedDecomposer().decompose(_brief(task_class="production_deployment"))

    assert result.disposition is DecompositionDisposition.REJECTED
    assert result.reason_codes == ("UNSUPPORTED_TASK_CLASS",)


def test_decomposer_requests_clarification_for_missing_mutation_scope() -> None:
    result = BoundedDecomposer().decompose(_brief(allowed_files=()))

    assert result.disposition is DecompositionDisposition.CLARIFICATION_REQUIRED
    assert result.reason_codes == ("TARGET_FILES_REQUIRED",)


def test_decomposer_enforces_maximum_steps() -> None:
    steps = [_step(step_id=f"step-{index}") for index in range(3)]
    result = BoundedDecomposer(max_steps=2).decompose(_brief(), proposed_steps=steps)

    assert result.reason_codes == ("MAXIMUM_STEP_COUNT_EXCEEDED",)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"repository_id": "other"}, "REPOSITORY_SCOPE_WIDENING"),
        ({"target_files": ("docs/current/other.md",)}, "FILE_SCOPE_WIDENING"),
        ({"target_services": ("workhub",)}, "SERVICE_SCOPE_WIDENING"),
        ({"requested_permissions": ("workhub.close",)}, "PERMISSION_SCOPE_WIDENING"),
        ({"acceptance_criterion_ids": ("ac-unknown",)}, "UNKNOWN_ACCEPTANCE_CRITERION"),
    ],
)
def test_decomposer_rejects_scope_widening(change: dict, reason: str) -> None:
    result = BoundedDecomposer().decompose(_brief(), proposed_steps=[_step(**change)])

    assert result.disposition is DecompositionDisposition.REJECTED
    assert result.reason_codes == (reason,)


def test_decomposer_rejects_incomplete_acceptance_mapping() -> None:
    result = BoundedDecomposer().decompose(
        _brief(),
        proposed_steps=[_step(acceptance_criterion_ids=("ac-1",))],
    )

    assert result.reason_codes == ("ACCEPTANCE_MAPPING_INCOMPLETE",)


def test_decomposer_rejects_path_escape() -> None:
    result = BoundedDecomposer().decompose(
        _brief(),
        proposed_steps=[_step(target_files=("../outside",))],
    )

    assert result.reason_codes == ("UNSAFE_TARGET_PATH",)


def test_verification_only_can_have_no_target_files() -> None:
    brief = _brief(task_class="verification_only", allowed_files=())
    result = BoundedDecomposer().decompose(brief)

    assert result.disposition is DecompositionDisposition.CANDIDATE
    assert all(not step.target_files for step in result.steps)
