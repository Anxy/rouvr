"""C042: PlanRevision/StepContract V2 bindings over V1 production contracts.

Does not replace PlanRevisionV1/StepContractV1. Adds required bindings and
revision lineage classification (ADR-005/ADR-006 style) for new revisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepContractV1

SCHEMA = "aix.plan_contract.v2"

PriorStepClass = Literal["COMPLETED", "PRESERVED", "INVALIDATED", "SUPERSEDED", "NOT_STARTED"]
Reversibility = Literal["FULL", "PARTIAL", "NONE"]
FailurePolicy = Literal["HALT", "REPAIR", "REPLAN", "SKIP_WITH_EVIDENCE"]


class PlanContractV2Error(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StepBindingsV2:
    step_id: str
    capability_ids: tuple[str, ...]
    test_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reversibility: Reversibility
    failure_policy: FailurePolicy
    context_manifest_ref: str | None = None
    effect_bounds: tuple[str, ...] = ()  # paths/services this step may mutate
    repository_snapshot_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriorStepClassification:
    step_id: str
    classification: PriorStepClass
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanRevisionV2:
    schema: str = SCHEMA
    base_plan: PlanRevisionV1 | None = None
    plan_id: str = ""
    revision: int = 1
    step_bindings: tuple[StepBindingsV2, ...] = ()
    prior_step_classifications: tuple[PriorStepClassification, ...] = ()
    strategy_comparison_ref: str | None = None
    created_from_plan_revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plan_id": self.plan_id,
            "revision": self.revision,
            "step_bindings": [b.to_dict() for b in self.step_bindings],
            "prior_step_classifications": [c.to_dict() for c in self.prior_step_classifications],
            "strategy_comparison_ref": self.strategy_comparison_ref,
            "created_from_plan_revision": self.created_from_plan_revision,
            "base_plan_id": self.base_plan.plan_id if self.base_plan else None,
        }


def validate_step_bindings(bindings: StepBindingsV2) -> None:
    if not bindings.capability_ids:
        raise PlanContractV2Error("CAPABILITY_REQUIRED", bindings.step_id)
    if not bindings.test_refs:
        raise PlanContractV2Error("TEST_REFS_REQUIRED", bindings.step_id)
    if not bindings.evidence_refs:
        raise PlanContractV2Error("EVIDENCE_REFS_REQUIRED", bindings.step_id)
    if not bindings.effect_bounds:
        raise PlanContractV2Error("EFFECT_BOUNDS_REQUIRED", bindings.step_id)


def bind_plan_revision_v2(
    *,
    plan: PlanRevisionV1,
    bindings: list[StepBindingsV2] | tuple[StepBindingsV2, ...],
    prior_step_classifications: list[PriorStepClassification] | tuple[PriorStepClassification, ...] = (),
    strategy_comparison_ref: str | None = None,
    created_from_plan_revision: str | None = None,
) -> PlanRevisionV2:
    """Attach V2 bindings to a V1 plan; fail closed if any step lacks required declarations."""
    by_id = {b.step_id: b for b in bindings}
    if len(by_id) != len(tuple(bindings)):
        raise PlanContractV2Error("DUPLICATE_STEP_BINDING")
    for step in plan.steps:
        b = by_id.get(step.id)
        if b is None:
            raise PlanContractV2Error("MISSING_STEP_BINDING", step.id)
        validate_step_bindings(b)
        # effect bounds must be subset of target_scope when overlapping as paths
        for bound in b.effect_bounds:
            if bound not in step.target_scope and not any(bound.startswith(f"{t}") for t in step.target_scope):
                # allow bound equal to any target_scope entry or declared relative
                if not any(t.endswith(bound) or bound.endswith(t) for t in step.target_scope):
                    raise PlanContractV2Error(
                        "EFFECT_BOUND_OUTSIDE_TARGET_SCOPE",
                        f"{step.id}:{bound}",
                    )

    if plan.revision > 1 or created_from_plan_revision:
        if not prior_step_classifications:
            raise PlanContractV2Error(
                "PRIOR_STEP_CLASSIFICATION_REQUIRED",
                "new revision must classify completed prior steps",
            )
        for c in prior_step_classifications:
            if not c.rationale.strip():
                raise PlanContractV2Error("PRIOR_STEP_RATIONALE_REQUIRED", c.step_id)

    return PlanRevisionV2(
        base_plan=plan,
        plan_id=plan.plan_id,
        revision=plan.revision,
        step_bindings=tuple(bindings),
        prior_step_classifications=tuple(prior_step_classifications),
        strategy_comparison_ref=strategy_comparison_ref,
        created_from_plan_revision=created_from_plan_revision or plan.created_from_plan_revision,
    )


def classify_prior_steps_from_v1(
    *,
    prior_plan: PlanRevisionV1,
    completed_step_ids: set[str] | frozenset[str],
    invalidated_step_ids: set[str] | frozenset[str] | None = None,
    superseded_step_ids: set[str] | frozenset[str] | None = None,
) -> tuple[PriorStepClassification, ...]:
    invalidated = invalidated_step_ids or set()
    superseded = superseded_step_ids or set()
    out: list[PriorStepClassification] = []
    for step in prior_plan.steps:
        if step.id in completed_step_ids:
            cls: PriorStepClass = "COMPLETED"
            rationale = "step completed under prior revision"
        elif step.id in invalidated:
            cls = "INVALIDATED"
            rationale = "prior effects invalidated by new evidence or scope change"
        elif step.id in superseded:
            cls = "SUPERSEDED"
            rationale = "replaced by a different step in the new revision"
        elif step.status == "VERIFIED":
            cls = "COMPLETED"
            rationale = f"status={step.status}"
        else:
            cls = "PRESERVED" if step.status == "PENDING" else "NOT_STARTED"
            rationale = f"carry-forward status={step.status}"
        out.append(PriorStepClassification(step_id=step.id, classification=cls, rationale=rationale))
    return tuple(out)
