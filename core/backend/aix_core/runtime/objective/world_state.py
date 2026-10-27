"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P6: Persistent Objective World State.

Assembles the already-existing per-objective caches `durable_api_backend.py` already
maintains (`ObjectiveObligationV1`s, `StepContractV1`, verification verdicts, step
attempts, `UnderstandingEvidenceV1`, replan/budget state) into one coherent,
queryable `ObjectiveWorldStateV1` snapshot -- same "reshape existing truth, never
recompute it" discipline `understanding_evidence.py`/`step_attempt.py`/
`cycle_outcome.py` already established for this codebase's other snapshot contracts.
The planner/a repair iteration is meant to reason from this record, not reconstructed
chat history or prior agent prose -- the sprint doc's own framing for this packet.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    ObjectiveWorldStateV1,
    ObligationStatus,
    StepAttemptV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome

_VERDICT_TO_OBLIGATION_STATUS: dict[str, ObligationStatus] = {
    "PASS": "SATISFIED",
    "REPAIR": "PARTIAL",
    "HALT": "FAILED",
    "REPLAN": "UNKNOWN",
    "REUNDERSTAND": "UNKNOWN",
}


def derive_obligation_status(
    *,
    obligation: ObjectiveObligationV1,
    step_contracts: tuple[StepContractV1, ...],
    verification_verdicts: tuple[SemanticVerificationOutcome, ...],
) -> ObligationStatus:
    """One obligation's status, derived from real evidence only:

    - Not referenced by any step's `obligation_refs` yet -> UNPLANNED (nothing has
      been planned to address it -- Packet P3's coverage gate is what stops execution
      while this is true for a *required* obligation).
    - Referenced, but no semantic verification has run yet -> PLANNED.
    - Referenced, and the most recent verification verdict for this objective was
      PASS/REPAIR/HALT/REPLAN -> SATISFIED/PARTIAL/FAILED/UNKNOWN respectively.

    Deliberately narrow, matching this codebase's current single-step-per-cycle
    architecture (same limitation `obligation_coverage.py` already documents): the
    "most recent verdict" is treated as applying to every obligation the one active
    step covers, since there is no per-obligation verification result yet -- a real,
    separate step-level verdict per obligation is a future packet's job once
    `StepContractV1[]` becomes a genuine multi-step plan (Packet P4).
    """
    covered = any(obligation.id in sc.obligation_refs for sc in step_contracts)
    if not covered:
        return "UNPLANNED"
    if not verification_verdicts:
        return "PLANNED"
    return _VERDICT_TO_OBLIGATION_STATUS.get(verification_verdicts[-1].verdict, "UNKNOWN")


def build_world_state(
    *,
    objective_id: str,
    cycle_id: str | None,
    objective: str,
    obligations: tuple[ObjectiveObligationV1, ...],
    step_contracts: tuple[StepContractV1, ...] = (),
    verification_verdicts: tuple[SemanticVerificationOutcome, ...] = (),
    step_attempts: tuple[StepAttemptV1, ...] = (),
    plan_revision: int = 0,
    understanding: UnderstandingEvidenceV1 | None = None,
    remaining_budget: BudgetV1 | None = None,
    retries_remaining: int | None = None,
    replans_remaining: int | None = None,
    intent_id: str | None = None,
    understanding_revision_id: str | None = None,
    plan_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    attempt_ids: tuple[str, ...] = (),
    provenance_known_facts: tuple[str, ...] | None = None,
) -> ObjectiveWorldStateV1:
    dated_obligations = tuple(
        obligation.model_copy(
            update={
                "status": derive_obligation_status(
                    obligation=obligation,
                    step_contracts=step_contracts,
                    verification_verdicts=verification_verdicts,
                )
            }
        )
        for obligation in obligations
    )
    remaining_obligation_ids = tuple(
        o.id for o in dated_obligations if o.required and o.status != "SATISFIED"
    )
    completed_step_ids = tuple(a.step_id for a in step_attempts if a.outcome == "SUCCEEDED")
    failed_attempt_count = sum(1 for a in step_attempts if a.outcome in ("FAILED", "TIMED_OUT"))
    verification_state = verification_verdicts[-1].verdict if verification_verdicts else None
    if intent_id is not None:
        known_facts = provenance_known_facts if provenance_known_facts is not None else evidence_ids
    else:
        known_facts = (
            tuple(understanding.files_read) + tuple(understanding.symbols_found) if understanding else ()
        )

    return ObjectiveWorldStateV1(
        objective_id=objective_id,
        cycle_id=cycle_id,
        objective=objective,
        obligations=dated_obligations,
        known_facts=known_facts,
        evidence_refs=understanding.evidence_refs if understanding and intent_id is None else evidence_ids,
        assumptions=understanding.assumptions if understanding else (),
        uncertainties=understanding.uncertainties if understanding else (),
        plan_revision=plan_revision,
        completed_step_ids=completed_step_ids,
        failed_attempt_count=failed_attempt_count,
        remaining_obligation_ids=remaining_obligation_ids,
        verification_state=verification_state,
        remaining_budget=remaining_budget,
        retries_remaining=retries_remaining,
        replans_remaining=replans_remaining,
        intent_id=intent_id,
        understanding_revision_id=understanding_revision_id,
        plan_id=plan_id,
        evidence_ids=evidence_ids,
        attempt_ids=attempt_ids,
    )


__all__ = ["derive_obligation_status", "build_world_state"]
