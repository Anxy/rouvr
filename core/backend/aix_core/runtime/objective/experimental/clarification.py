"""Validated human clarification and bounded planning-resume inputs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CLARIFICATION_SCHEMA_VERSION = "aix.objective-cycle.clarification.v1"


class ResponseKind(StrEnum):
    TEXT = "TEXT"
    CHOICE = "CHOICE"
    STRING_LIST = "STRING_LIST"


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    response_kind: ResponseKind
    required: bool = True
    allowed_values: tuple[str, ...] = ()
    max_length: int = Field(default=2000, ge=1, le=10000)
    scope_dimension: Literal["files", "services", "permissions"] | None = None


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CLARIFICATION_SCHEMA_VERSION
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    objective_version: int = Field(ge=1)
    base_objective_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    questions: tuple[ClarificationQuestion, ...] = Field(min_length=1)


class ClarificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    objective_version: int = Field(ge=1)
    actor_id: str = Field(min_length=1)
    actor_type: Literal["HUMAN"]
    submitted_at: datetime
    answers: dict[str, str | list[str]]


class PlanningResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CLARIFICATION_SCHEMA_VERSION
    request_id: str
    tenant_id: str
    objective_id: str
    objective_version: int
    actor_id: str
    base_objective_sha256: str
    base_plan_sha256: str | None
    validated_answers: dict[str, str | tuple[str, ...]]
    replan_requested: bool
    approval_required: bool
    resume_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClarificationValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ClarificationValidator:
    def validate(
        self,
        *,
        request: ClarificationRequest,
        response: ClarificationResponse,
    ) -> PlanningResumeInput:
        if request.request_id != response.request_id:
            raise ClarificationValidationError("CLARIFICATION_REQUEST_MISMATCH")
        if request.tenant_id != response.tenant_id:
            raise ClarificationValidationError("CLARIFICATION_TENANT_MISMATCH")
        if request.objective_id != response.objective_id:
            raise ClarificationValidationError("CLARIFICATION_OBJECTIVE_MISMATCH")
        if request.objective_version != response.objective_version:
            raise ClarificationValidationError("CLARIFICATION_OBJECTIVE_VERSION_MISMATCH")
        if request.issued_at.tzinfo is None or request.expires_at.tzinfo is None or response.submitted_at.tzinfo is None:
            raise ClarificationValidationError("CLARIFICATION_TIMESTAMP_TZ_REQUIRED")
        if response.submitted_at < request.issued_at:
            raise ClarificationValidationError("CLARIFICATION_RESPONSE_PREDATES_REQUEST")
        if response.submitted_at > request.expires_at:
            raise ClarificationValidationError("CLARIFICATION_REQUEST_EXPIRED")

        questions = {question.question_id: question for question in request.questions}
        if len(questions) != len(request.questions):
            raise ClarificationValidationError("DUPLICATE_CLARIFICATION_QUESTION_ID")
        extra = set(response.answers) - set(questions)
        if extra:
            raise ClarificationValidationError("UNEXPECTED_CLARIFICATION_ANSWER")
        missing = {
            question_id
            for question_id, question in questions.items()
            if question.required and question_id not in response.answers
        }
        if missing:
            raise ClarificationValidationError("REQUIRED_CLARIFICATION_ANSWER_MISSING")

        validated: dict[str, str | tuple[str, ...]] = {}
        approval_required = False
        for question_id in sorted(response.answers):
            question = questions[question_id]
            value = response.answers[question_id]
            validated[question_id] = self._validate_answer(question, value)
            approval_required = approval_required or question.scope_dimension is not None

        payload: dict[str, Any] = {
            "schema_version": CLARIFICATION_SCHEMA_VERSION,
            "request_id": request.request_id,
            "tenant_id": request.tenant_id,
            "objective_id": request.objective_id,
            "objective_version": request.objective_version,
            "actor_id": response.actor_id,
            "base_objective_sha256": request.base_objective_sha256,
            "base_plan_sha256": request.base_plan_sha256,
            "validated_answers": validated,
            "replan_requested": request.base_plan_sha256 is not None,
            "approval_required": approval_required,
        }
        return PlanningResumeInput(**payload, resume_input_sha256=_hash(payload))

    @staticmethod
    def _validate_answer(
        question: ClarificationQuestion,
        value: str | list[str],
    ) -> str | tuple[str, ...]:
        if question.response_kind is ResponseKind.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ClarificationValidationError("CLARIFICATION_TEXT_REQUIRED")
            normalized = " ".join(value.split())
            if len(normalized) > question.max_length:
                raise ClarificationValidationError("CLARIFICATION_TEXT_TOO_LONG")
            return normalized
        if question.response_kind is ResponseKind.CHOICE:
            if not isinstance(value, str) or value not in question.allowed_values:
                raise ClarificationValidationError("CLARIFICATION_CHOICE_INVALID")
            return value
        if question.response_kind is ResponseKind.STRING_LIST:
            if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ClarificationValidationError("CLARIFICATION_STRING_LIST_INVALID")
            normalized_values = tuple(sorted({item.strip() for item in value}))
            if question.allowed_values and not set(normalized_values) <= set(question.allowed_values):
                raise ClarificationValidationError("CLARIFICATION_SCOPE_VALUE_NOT_ALLOWED")
            return normalized_values
        raise ClarificationValidationError("CLARIFICATION_RESPONSE_KIND_UNSUPPORTED")
