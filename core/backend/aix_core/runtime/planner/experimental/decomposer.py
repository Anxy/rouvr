"""Bounded objective decomposition into non-executable candidate steps."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.governance.policy import PolicyDisposition, TaskClassPolicy

DECOMPOSER_VERSION = "aix.objective-cycle.decomposer.v1"


class DecompositionDisposition(StrEnum):
    CANDIDATE = "CANDIDATE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REJECTED = "REJECTED"


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ObjectiveBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    allowed_files: tuple[str, ...] = ()
    allowed_services: tuple[str, ...] = ()
    allowed_permissions: tuple[str, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(min_length=1)
    context_capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CandidateStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    target_files: tuple[str, ...] = ()
    target_services: tuple[str, ...] = ()
    requested_permissions: tuple[str, ...] = ()
    acceptance_criterion_ids: tuple[str, ...] = Field(min_length=1)
    dependency_ids: tuple[str, ...] = ()
    required_context_item_ids: tuple[str, ...] = ()


class DecompositionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decomposer_version: str = DECOMPOSER_VERSION
    disposition: DecompositionDisposition
    reason_codes: tuple[str, ...] = Field(min_length=1)
    objective_id: str
    policy_version: str
    steps: tuple[CandidateStep, ...] = ()
    decomposition_sha256: str | None = None


def _normalized_path(path: str) -> str:
    raw = str(path).replace("\\", "/").strip()
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"UNSAFE_TARGET_PATH:{raw}")
    return candidate.as_posix()


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BoundedDecomposer:
    def __init__(self, *, max_steps: int = 12, policy: TaskClassPolicy | None = None) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.max_steps = max_steps
        self.policy = policy or TaskClassPolicy()

    def decompose(
        self,
        brief: ObjectiveBrief,
        *,
        proposed_steps: list[CandidateStep] | None = None,
    ) -> DecompositionResult:
        policy_decision = self.policy.evaluate(task_class=brief.task_class)
        if policy_decision.disposition is PolicyDisposition.REJECT:
            return self._result(
                brief,
                DecompositionDisposition.REJECTED,
                policy_decision.reason_codes,
                policy_decision.policy_version,
            )
        if policy_decision.disposition is PolicyDisposition.CLARIFY:
            return self._result(
                brief,
                DecompositionDisposition.CLARIFICATION_REQUIRED,
                policy_decision.reason_codes,
                policy_decision.policy_version,
            )
        if brief.task_class != "verification_only" and not brief.allowed_files:
            return self._result(
                brief,
                DecompositionDisposition.CLARIFICATION_REQUIRED,
                ("TARGET_FILES_REQUIRED",),
                policy_decision.policy_version,
            )
        criterion_ids = tuple(criterion.criterion_id for criterion in brief.acceptance_criteria)
        if len(criterion_ids) != len(set(criterion_ids)):
            return self._result(
                brief,
                DecompositionDisposition.REJECTED,
                ("DUPLICATE_ACCEPTANCE_CRITERION_ID",),
                policy_decision.policy_version,
            )

        steps = tuple(proposed_steps or self._default_steps(brief))
        if len(steps) > self.max_steps:
            return self._result(
                brief,
                DecompositionDisposition.REJECTED,
                ("MAXIMUM_STEP_COUNT_EXCEEDED",),
                policy_decision.policy_version,
            )
        if not steps:
            return self._result(
                brief,
                DecompositionDisposition.CLARIFICATION_REQUIRED,
                ("CANDIDATE_STEPS_REQUIRED",),
                policy_decision.policy_version,
            )
        reason = self._validate_scope(brief, steps)
        if reason:
            return self._result(
                brief,
                DecompositionDisposition.REJECTED,
                (reason,),
                policy_decision.policy_version,
            )

        covered = {criterion_id for step in steps for criterion_id in step.acceptance_criterion_ids}
        expected = set(criterion_ids)
        if covered != expected:
            return self._result(
                brief,
                DecompositionDisposition.REJECTED,
                ("ACCEPTANCE_MAPPING_INCOMPLETE",),
                policy_decision.policy_version,
            )
        payload = [
            step.model_dump(mode="json")
            for step in sorted(steps, key=lambda candidate: candidate.step_id)
        ]
        return DecompositionResult(
            disposition=DecompositionDisposition.CANDIDATE,
            reason_codes=("BOUNDED_CANDIDATE_STEPS",),
            objective_id=brief.objective_id,
            policy_version=policy_decision.policy_version,
            steps=steps,
            decomposition_sha256=_canonical_hash(payload),
        )

    def _default_steps(self, brief: ObjectiveBrief) -> list[CandidateStep]:
        files = tuple(_normalized_path(path) for path in brief.allowed_files)
        return [
            CandidateStep(
                step_id=f"step-{index:03d}",
                purpose=criterion.description,
                repository_id=brief.repository_id,
                target_files=files,
                target_services=brief.allowed_services,
                acceptance_criterion_ids=(criterion.criterion_id,),
            )
            for index, criterion in enumerate(brief.acceptance_criteria, start=1)
        ]

    @staticmethod
    def _validate_scope(brief: ObjectiveBrief, steps: tuple[CandidateStep, ...]) -> str | None:
        allowed_files = {_normalized_path(path) for path in brief.allowed_files}
        allowed_services = set(brief.allowed_services)
        allowed_permissions = set(brief.allowed_permissions)
        expected_criteria = {criterion.criterion_id for criterion in brief.acceptance_criteria}
        step_ids = [step.step_id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            return "DUPLICATE_STEP_ID"
        for step in steps:
            if step.repository_id != brief.repository_id:
                return "REPOSITORY_SCOPE_WIDENING"
            try:
                target_files = {_normalized_path(path) for path in step.target_files}
            except ValueError:
                return "UNSAFE_TARGET_PATH"
            if not target_files <= allowed_files:
                return "FILE_SCOPE_WIDENING"
            if not set(step.target_services) <= allowed_services:
                return "SERVICE_SCOPE_WIDENING"
            if not set(step.requested_permissions) <= allowed_permissions:
                return "PERMISSION_SCOPE_WIDENING"
            if not set(step.acceptance_criterion_ids) <= expected_criteria:
                return "UNKNOWN_ACCEPTANCE_CRITERION"
        return None

    @staticmethod
    def _result(
        brief: ObjectiveBrief,
        disposition: DecompositionDisposition,
        reason_codes: tuple[str, ...],
        policy_version: str,
    ) -> DecompositionResult:
        return DecompositionResult(
            disposition=disposition,
            reason_codes=reason_codes,
            objective_id=brief.objective_id,
            policy_version=policy_version,
        )
