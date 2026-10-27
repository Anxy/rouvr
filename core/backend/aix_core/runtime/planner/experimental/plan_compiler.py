"""Compile bounded candidate steps into a deterministic acyclic v1 plan."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.planner.experimental.decomposer import (
    CandidateStep,
    DecompositionDisposition,
    DecompositionResult,
    ObjectiveBrief,
)

PLAN_SCHEMA_VERSION = "aix.objective-cycle.plan.v1"


class PlanCompileError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CompiledStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    sequence: int = Field(ge=1)
    purpose: str
    repository_id: str
    target_files: tuple[str, ...]
    target_services: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    acceptance_criterion_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    required_context_item_ids: tuple[str, ...]


class CompiledPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PLAN_SCHEMA_VERSION
    objective_id: str
    tenant_id: str
    repository_id: str
    context_capsule_sha256: str
    decomposition_sha256: str
    execution_mode: str = "SEQUENTIAL_V1"
    steps: tuple[CompiledStep, ...] = Field(min_length=1)
    acceptance_map: dict[str, tuple[str, ...]]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DagPlanCompiler:
    def __init__(self, *, max_steps: int = 12) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.max_steps = max_steps

    def compile(self, *, brief: ObjectiveBrief, decomposition: DecompositionResult) -> CompiledPlan:
        if decomposition.disposition is not DecompositionDisposition.CANDIDATE:
            raise PlanCompileError("DECOMPOSITION_NOT_COMPILABLE")
        if decomposition.objective_id != brief.objective_id:
            raise PlanCompileError("OBJECTIVE_ID_MISMATCH")
        if not decomposition.decomposition_sha256:
            raise PlanCompileError("DECOMPOSITION_HASH_REQUIRED")
        steps = decomposition.steps
        if not steps:
            raise PlanCompileError("EMPTY_PLAN")
        if len(steps) > self.max_steps:
            raise PlanCompileError("MAXIMUM_STEP_COUNT_EXCEEDED")
        decomposition_payload = [
            step.model_dump(mode="json")
            for step in sorted(steps, key=lambda candidate: candidate.step_id)
        ]
        if _canonical_hash(decomposition_payload) != decomposition.decomposition_sha256:
            raise PlanCompileError("DECOMPOSITION_HASH_MISMATCH")

        by_id = {step.step_id: step for step in steps}
        if len(by_id) != len(steps):
            raise PlanCompileError("DUPLICATE_STEP_ID")
        all_ids = set(by_id)
        for step in steps:
            dependencies = set(step.dependency_ids)
            if step.step_id in dependencies:
                raise PlanCompileError("SELF_DEPENDENCY")
            if not dependencies <= all_ids:
                raise PlanCompileError("MISSING_DEPENDENCY")

        ordered_ids = self._topological_order(by_id)
        expected_criteria = {criterion.criterion_id for criterion in brief.acceptance_criteria}
        acceptance_map: dict[str, tuple[str, ...]] = {}
        for criterion_id in sorted(expected_criteria):
            mapped = tuple(
                step_id for step_id in ordered_ids if criterion_id in by_id[step_id].acceptance_criterion_ids
            )
            if not mapped:
                raise PlanCompileError("ACCEPTANCE_MAPPING_INCOMPLETE")
            acceptance_map[criterion_id] = mapped
        for step in steps:
            if not set(step.acceptance_criterion_ids) <= expected_criteria:
                raise PlanCompileError("UNKNOWN_ACCEPTANCE_CRITERION")

        compiled_steps = tuple(
            self._compiled_step(by_id[step_id], sequence=index)
            for index, step_id in enumerate(ordered_ids, start=1)
        )
        payload: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "objective_id": brief.objective_id,
            "tenant_id": brief.tenant_id,
            "repository_id": brief.repository_id,
            "context_capsule_sha256": brief.context_capsule_sha256,
            "decomposition_sha256": decomposition.decomposition_sha256,
            "execution_mode": "SEQUENTIAL_V1",
            "steps": [step.model_dump(mode="json") for step in compiled_steps],
            "acceptance_map": acceptance_map,
        }
        return CompiledPlan(**payload, plan_sha256=_canonical_hash(payload))

    @staticmethod
    def _topological_order(by_id: dict[str, CandidateStep]) -> tuple[str, ...]:
        remaining_dependencies = {
            step_id: set(step.dependency_ids) for step_id, step in by_id.items()
        }
        ready = sorted(step_id for step_id, deps in remaining_dependencies.items() if not deps)
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for candidate_id in sorted(remaining_dependencies):
                dependencies = remaining_dependencies[candidate_id]
                if current in dependencies:
                    dependencies.remove(current)
                    if not dependencies and candidate_id not in ordered and candidate_id not in ready:
                        ready.append(candidate_id)
                        ready.sort()
        if len(ordered) != len(by_id):
            raise PlanCompileError("CYCLIC_DEPENDENCY_GRAPH")
        return tuple(ordered)

    @staticmethod
    def _compiled_step(step: CandidateStep, *, sequence: int) -> CompiledStep:
        return CompiledStep(
            step_id=step.step_id,
            sequence=sequence,
            purpose=step.purpose,
            repository_id=step.repository_id,
            target_files=tuple(sorted(step.target_files)),
            target_services=tuple(sorted(step.target_services)),
            requested_permissions=tuple(sorted(step.requested_permissions)),
            acceptance_criterion_ids=tuple(sorted(step.acceptance_criterion_ids)),
            dependency_ids=tuple(sorted(step.dependency_ids)),
            required_context_item_ids=tuple(sorted(step.required_context_item_ids)),
        )
