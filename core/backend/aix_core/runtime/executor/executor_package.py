"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P7: Step Context & Execution Package
Hardening.

Builds the `ExecutorPackageV1` record -- the complete, authoritative bundle of what
one executor dispatch actually received -- from already-built contracts
(`StepContractV1`, `StepContextCapsuleV1`). Never a second, competing computation of
their content; this module only bundles references and the few boundary fields
(objective text, acceptance criteria, forbidden effects) that do not live on either
contract. Deliberately no accumulated conversation transcript, no append-only
instructions -- every field is a typed, bounded value.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    ExecutorPackageV1,
    ObjectiveEnvelopeV1,
    StepContextCapsuleV1,
    StepContractV1,
)


def build_executor_package(
    *,
    cycle_id: str,
    objective_id: str,
    objective: str,
    envelope: ObjectiveEnvelopeV1 | None,
    step_contract: StepContractV1 | None,
    context_capsule: StepContextCapsuleV1 | None,
    repair_context: str | None,
    plan_id: str | None = None,
    plan_revision: int | None = None,
    executor_profile_id: str | None = None,
    approval_ref: str | None = None,
    target_paths: tuple[str, ...] = (),
) -> ExecutorPackageV1:
    return ExecutorPackageV1(
        cycle_id=cycle_id,
        objective_id=objective_id,
        objective_boundary=objective,
        acceptance_criteria=envelope.acceptance_criteria if envelope is not None else (),
        forbidden_effects=envelope.forbidden_effects if envelope is not None else (),
        step_contract_id=step_contract.id if step_contract is not None else None,
        obligation_ids=step_contract.obligation_refs if step_contract is not None else (),
        context_capsule_id=context_capsule.id if context_capsule is not None else None,
        repair_context=repair_context,
        plan_id=plan_id,
        plan_revision=plan_revision,
        executor_profile_id=executor_profile_id,
        approval_ref=approval_ref,
        target_paths=target_paths,
    )


__all__ = ["build_executor_package"]
