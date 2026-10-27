from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.experimental.clarification import (
    ClarificationQuestion,
    ClarificationRequest,
    ClarificationResponse,
    ClarificationValidationError,
    ClarificationValidator,
    ResponseKind,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def _request(*, with_plan: bool = False) -> ClarificationRequest:
    return ClarificationRequest(
        request_id="clarify-1",
        tenant_id="tenant-1",
        objective_id="objective-1",
        objective_version=3,
        base_objective_sha256=HASH,
        base_plan_sha256="b" * 64 if with_plan else None,
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        questions=(
            ClarificationQuestion(
                question_id="target",
                prompt="Which approved file is in scope?",
                response_kind=ResponseKind.STRING_LIST,
                allowed_values=("docs/a.md", "docs/b.md"),
                scope_dimension="files",
            ),
            ClarificationQuestion(
                question_id="intent",
                prompt="Describe the bounded intent.",
                response_kind=ResponseKind.TEXT,
            ),
        ),
    )


def _response(**changes) -> ClarificationResponse:
    payload = {
        "request_id": "clarify-1",
        "tenant_id": "tenant-1",
        "objective_id": "objective-1",
        "objective_version": 3,
        "actor_id": "human-1",
        "actor_type": "HUMAN",
        "submitted_at": NOW + timedelta(minutes=5),
        "answers": {"target": ["docs/a.md"], "intent": "Update only the bounded documentation."},
    }
    payload.update(changes)
    return ClarificationResponse(**payload)


def test_validator_builds_hashed_planning_resume_input() -> None:
    resume = ClarificationValidator().validate(request=_request(), response=_response())

    assert resume.validated_answers["target"] == ("docs/a.md",)
    assert resume.approval_required is True
    assert resume.replan_requested is False
    assert len(resume.resume_input_sha256) == 64


def test_validator_marks_existing_plan_as_replan() -> None:
    resume = ClarificationValidator().validate(request=_request(with_plan=True), response=_response())

    assert resume.replan_requested is True
    assert resume.base_plan_sha256 == "b" * 64


def test_resume_input_is_deterministic() -> None:
    validator = ClarificationValidator()
    left = validator.validate(request=_request(), response=_response())
    right = validator.validate(
        request=_request(),
        response=_response(answers={"intent": "Update only the bounded documentation.", "target": ["docs/a.md"]}),
    )

    assert left.model_dump(mode="json") == right.model_dump(mode="json")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"request_id": "other"}, "CLARIFICATION_REQUEST_MISMATCH"),
        ({"tenant_id": "other"}, "CLARIFICATION_TENANT_MISMATCH"),
        ({"objective_id": "other"}, "CLARIFICATION_OBJECTIVE_MISMATCH"),
        ({"objective_version": 4}, "CLARIFICATION_OBJECTIVE_VERSION_MISMATCH"),
    ],
)
def test_validator_rejects_identity_or_version_mismatch(change: dict, reason: str) -> None:
    with pytest.raises(ClarificationValidationError, match=reason):
        ClarificationValidator().validate(request=_request(), response=_response(**change))


def test_validator_rejects_expired_response() -> None:
    with pytest.raises(ClarificationValidationError, match="CLARIFICATION_REQUEST_EXPIRED"):
        ClarificationValidator().validate(
            request=_request(),
            response=_response(submitted_at=NOW + timedelta(hours=2)),
        )


def test_validator_rejects_missing_required_answer() -> None:
    with pytest.raises(ClarificationValidationError, match="REQUIRED_CLARIFICATION_ANSWER_MISSING"):
        ClarificationValidator().validate(
            request=_request(),
            response=_response(answers={"intent": "bounded"}),
        )


def test_validator_rejects_extra_answer() -> None:
    with pytest.raises(ClarificationValidationError, match="UNEXPECTED_CLARIFICATION_ANSWER"):
        ClarificationValidator().validate(
            request=_request(),
            response=_response(
                answers={
                    "target": ["docs/a.md"],
                    "intent": "bounded",
                    "approve": "yes",
                }
            ),
        )


def test_validator_rejects_scope_value_outside_allowlist() -> None:
    with pytest.raises(ClarificationValidationError, match="CLARIFICATION_SCOPE_VALUE_NOT_ALLOWED"):
        ClarificationValidator().validate(
            request=_request(),
            response=_response(answers={"target": ["../outside"], "intent": "bounded"}),
        )


def test_response_contract_rejects_non_human_actor_type() -> None:
    with pytest.raises(ValidationError):
        _response(actor_type="AGENT")
