"""Lossless adapters from canonical runtime contracts to F4 execution views."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from backend.aix_core.framework.execution.models import (
    Attempt,
    ExecutionLane,
    ExecutionObjectiveRef,
    ExecutionPlan,
    Step,
)
from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    PlanRevisionV1,
    StepAttemptV1,
    StepContractV1,
)
if TYPE_CHECKING:
    from backend.aix_core.runtime.objective.durable_aggregate.attempt_journal import AttemptJournalEntry
else:
    AttemptJournalEntry = Any


def from_objective_envelope(envelope: ObjectiveEnvelopeV1) -> ExecutionObjectiveRef:
    return ExecutionObjectiveRef(
        objective_id=envelope.id,
        tenant_id=envelope.tenant_id,
        actor_ref=envelope.actor_ref,
        project_id=envelope.project_id,
        workhub_task_ref=envelope.workhub_task_ref,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        idempotency_key=envelope.idempotency_key,
        repository_scope=envelope.repository_scope,
    )


def from_step_contract(step: StepContractV1) -> Step:
    return Step(
        step_id=step.id,
        index=step.step_index,
        purpose=step.purpose,
        depends_on=step.depends_on,
        target_scope=step.target_scope,
        acceptance_criteria=step.acceptance_criteria,
        validation_commands=step.validation_commands,
        required_effects=step.required_effects,
        required_evidence=step.required_evidence,
        risk_tier=str(step.risk_tier.value if hasattr(step.risk_tier, "value") else step.risk_tier),
        approval_required=step.approval_required,
        status=step.status,
        source_contract=step,
    )


def _lane_layers(steps: Iterable[StepContractV1], *, plan_id: str) -> tuple[ExecutionLane, ...]:
    ordered = tuple(steps)
    by_id = {step.id: step for step in ordered}
    depth: dict[str, int] = {}

    def visit(step_id: str, visiting: frozenset[str] = frozenset()) -> int:
        if step_id in depth:
            return depth[step_id]
        if step_id in visiting:
            raise ValueError(f"circular step dependency involving {step_id!r}")
        step = by_id.get(step_id)
        if step is None:
            raise ValueError(f"unknown step dependency {step_id!r}")
        value = 0 if not step.depends_on else 1 + max(
            visit(dependency, visiting | {step_id}) for dependency in step.depends_on
        )
        depth[step_id] = value
        return value

    for step in ordered:
        visit(step.id)
    return tuple(
        ExecutionLane(
            lane_id=f"{plan_id}:lane:{ordinal}",
            ordinal=ordinal,
            step_ids=tuple(step.id for step in ordered if depth[step.id] == ordinal),
        )
        for ordinal in range(max(depth.values()) + 1)
    )


def from_plan_revision(plan: PlanRevisionV1) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=plan.plan_id,
        objective_id=plan.objective_id,
        cycle_id=plan.cycle_id,
        revision=plan.revision,
        status=plan.status,
        lanes=_lane_layers(plan.steps, plan_id=plan.plan_id),
        step_ids=tuple(step.id for step in plan.steps),
        source_contract_ref=f"runtime://PlanRevisionV1/{plan.plan_id}@{plan.revision}",
        approval_ref=plan.approval_ref,
    )


def from_attempt_journal(
    entry: AttemptJournalEntry,
    attempt: StepAttemptV1 | None = None,
) -> Attempt:
    if attempt is not None:
        if attempt.id != entry.attempt_id or attempt.step_id != entry.step_id:
            raise ValueError("journal entry and StepAttemptV1 identities do not match")
        if attempt.attempt_number != entry.attempt_number:
            raise ValueError("journal entry and StepAttemptV1 attempt numbers do not match")

    evidence_refs: list[str] = []
    payload_refs = entry.payload.get("evidence_refs")
    if isinstance(payload_refs, (list, tuple)):
        evidence_refs.extend(str(ref) for ref in payload_refs if str(ref).strip())
    if attempt is not None:
        evidence_refs.extend(attempt.artifact_refs)
        if attempt.verification_result_ref:
            evidence_refs.append(attempt.verification_result_ref)
    evidence_refs = list(dict.fromkeys(evidence_refs))

    result_ref = attempt.raw_result_ref if attempt is not None else None
    payload_result_ref = entry.payload.get("result_ref")
    if result_ref is None and isinstance(payload_result_ref, str) and payload_result_ref.strip():
        result_ref = payload_result_ref

    outcome = entry.outcome
    if entry.status in {"ABORTED", "ORPHAN_COMPENSATED"}:
        outcome = "ABORTED"
    return Attempt(
        attempt_id=entry.attempt_id,
        step_id=entry.step_id,
        number=entry.attempt_number,
        command_id=entry.command_id,
        status=entry.status,
        outcome=outcome,
        effects_applied=entry.effects_applied,
        started_at=entry.opened_at,
        completed_at=entry.committed_at,
        evidence_refs=tuple(evidence_refs),
        result_ref=result_ref,
    )


__all__ = [
    "from_attempt_journal",
    "from_objective_envelope",
    "from_plan_revision",
    "from_step_contract",
]
