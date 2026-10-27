"""Autonomous Closed-Loop Lane, Packet AL-05: build a `StepContextCapsuleV1` -- the
bounded, step-specific slice of an `UnderstandingEvidenceV1` snapshot a persona
actually needs, not the complete exploration history.

Same "reshape existing evidence, never re-derive it" discipline as
`understanding_evidence.py`/`step_attempt.py`: this module filters and carries
provenance, it does not gather new evidence.
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import StepContextCapsuleV1, UnderstandingEvidenceV1


def build_step_context_capsule(
    *,
    step_id: str,
    understanding: UnderstandingEvidenceV1 | None,
    target_scope: tuple[str, ...] = (),
    invariants: tuple[str, ...] = (),
    relevant_previous_failure: str | None = None,
    intent_id: str | None = None,
    intent_text: str | None = None,
    cited_claim_ids: tuple[str, ...] = (),
    cited_evidence_ids: tuple[str, ...] = (),
    plan_target_paths: tuple[str, ...] = (),
    plan_id: str | None = None,
) -> StepContextCapsuleV1:
    """Filter evidence to one step. Provenance capsules use Intent + cited claims +
    cited evidence + accepted PlanTargets — never observed_problem or files_read ∩ mixed scope.
    """
    if intent_id or plan_target_paths or cited_evidence_ids:
        return StepContextCapsuleV1(
            step_id=step_id,
            understanding_evidence_id=None,
            relevant_files=plan_target_paths or target_scope,
            relevant_symbols=(),
            relevant_evidence_refs=cited_evidence_ids,
            dependencies=(),
            invariants=invariants,
            relevant_previous_failure=relevant_previous_failure,
            intent_id=intent_id,
            intent_text=intent_text,
            cited_claim_ids=cited_claim_ids,
            cited_evidence_ids=cited_evidence_ids,
            plan_target_paths=plan_target_paths or target_scope,
            plan_id=plan_id,
        )
    if understanding is None:
        return StepContextCapsuleV1(
            step_id=step_id,
            understanding_evidence_id=None,
            invariants=invariants,
            relevant_previous_failure=relevant_previous_failure,
        )

    scope_set = set(target_scope)
    relevant_files = (
        tuple(f for f in understanding.files_read if f in scope_set) if scope_set else understanding.files_read
    )
    relevant_symbols = (
        tuple(s for s in understanding.symbols_found if s in scope_set) if scope_set else understanding.symbols_found
    )

    return StepContextCapsuleV1(
        step_id=step_id,
        understanding_evidence_id=understanding.id,
        relevant_files=relevant_files,
        relevant_symbols=relevant_symbols,
        relevant_evidence_refs=understanding.evidence_refs,
        dependencies=understanding.dependencies,
        invariants=invariants,
        relevant_previous_failure=relevant_previous_failure,
        intent_id=intent_id,
        intent_text=intent_text,
        cited_claim_ids=cited_claim_ids,
        cited_evidence_ids=cited_evidence_ids,
        plan_target_paths=plan_target_paths,
        plan_id=plan_id,
    )


__all__ = ["build_step_context_capsule"]
