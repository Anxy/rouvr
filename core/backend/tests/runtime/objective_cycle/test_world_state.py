"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P6: Persistent Objective World State.

Unit tests for `world_state.py` -- pure, deterministic reshaping of already-existing
per-objective caches, no model call, no backend needed.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    StepContractV1,
)
from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome
from backend.aix_core.runtime.objective.world_state import build_world_state, derive_obligation_status


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def _step(*, obligation_refs: tuple[str, ...]) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1", tenant_id="t", step_index=0, purpose="x", target_scope=("a.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit", acceptance_criteria=("x",),
        validation_commands=("x",), assigned_role="executor", step_budget=_budget(),
        obligation_refs=obligation_refs, status="PENDING",
    )


def _verdict(verdict: str) -> SemanticVerificationOutcome:
    return SemanticVerificationOutcome(
        verdict=verdict, reasons=(f"{verdict} reason",), mechanical_passed=True, semantic_checked=True,
    )


def test_unplanned_when_no_step_contract_references_it() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    assert derive_obligation_status(obligation=ob, step_contracts=(), verification_verdicts=()) == "UNPLANNED"


def test_planned_when_referenced_but_not_yet_verified() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    assert derive_obligation_status(obligation=ob, step_contracts=(step,), verification_verdicts=()) == "PLANNED"


def test_satisfied_when_referenced_and_latest_verdict_is_pass() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    status = derive_obligation_status(
        obligation=ob, step_contracts=(step,), verification_verdicts=(_verdict("PASS"),)
    )
    assert status == "SATISFIED"


def test_partial_when_referenced_and_latest_verdict_is_repair() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    status = derive_obligation_status(
        obligation=ob, step_contracts=(step,), verification_verdicts=(_verdict("REPAIR"),)
    )
    assert status == "PARTIAL"


def test_failed_when_referenced_and_latest_verdict_is_halt() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    status = derive_obligation_status(
        obligation=ob, step_contracts=(step,), verification_verdicts=(_verdict("HALT"),)
    )
    assert status == "FAILED"


def test_unknown_when_referenced_and_latest_verdict_is_replan() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    status = derive_obligation_status(
        obligation=ob, step_contracts=(step,), verification_verdicts=(_verdict("REPLAN"),)
    )
    assert status == "UNKNOWN"


def test_only_the_most_recent_verdict_is_consulted() -> None:
    ob = ObjectiveObligationV1(objective_id="obj-1", description="x")
    step = _step(obligation_refs=(ob.id,))
    status = derive_obligation_status(
        obligation=ob, step_contracts=(step,), verification_verdicts=(_verdict("REPAIR"), _verdict("PASS"))
    )
    assert status == "SATISFIED"


def test_build_world_state_matches_the_sprint_docs_worked_example_shape() -> None:
    """Doc's own example: OB-1 SATISFIED, OB-2 PARTIAL, OB-3 UNPLANNED, OB-4 SATISFIED,
    OB-5 UNKNOWN."""
    ob1, ob2, ob3, ob4, ob5 = (
        ObjectiveObligationV1(objective_id="obj-1", description=f"OB-{n}") for n in range(1, 6)
    )
    step = _step(obligation_refs=(ob1.id, ob2.id, ob4.id, ob5.id))  # ob3 dropped by the planner

    world_state = build_world_state(
        objective_id="obj-1",
        cycle_id="cyc-1",
        objective="refactor CronJobController.php",
        obligations=(ob1, ob2, ob3, ob4, ob5),
        step_contracts=(step,),
        # Only the most recent verdict is consulted (a real, separate per-obligation
        # verdict is a documented future-packet limitation) -- constructed here purely
        # to exercise the assembly/remaining-ids logic, not to claim OB-1..OB-5 each
        # got their own independent verification.
        verification_verdicts=(_verdict("PASS"),),
        plan_revision=2,
        retries_remaining=1,
        replans_remaining=2,
    )
    statuses = {o.description: o.status for o in world_state.obligations}
    assert statuses["OB-1"] == "SATISFIED"
    assert statuses["OB-3"] == "UNPLANNED"
    # OB-3 (never referenced by any step) is required and not SATISFIED -> remaining.
    assert ob3.id in world_state.remaining_obligation_ids
    assert ob1.id not in world_state.remaining_obligation_ids
    assert world_state.plan_revision == 2
    assert world_state.retries_remaining == 1
    assert world_state.replans_remaining == 2
    assert world_state.verification_state == "PASS"


def test_no_understanding_yields_empty_known_facts_not_a_crash() -> None:
    world_state = build_world_state(
        objective_id="obj-1", cycle_id=None, objective="x", obligations=(), understanding=None,
    )
    assert world_state.known_facts == ()
    assert world_state.evidence_refs == ()
