"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P3: Objective Fidelity / Obligation
Coverage. `validate_obligation_coverage()` is the deterministic, no-model gate --
these tests exercise it directly, no planner/model involved.
"""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveObligationV1,
    StepContractV1,
)
from backend.aix_core.runtime.verification.obligation_coverage import validate_obligation_coverage


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def _step(*, obligation_refs: tuple[str, ...]) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t",
        step_index=0,
        purpose="do the work",
        target_scope=("CronJobController.php",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("passes",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=obligation_refs,
        status="PENDING",
    )


def _obligation(description: str, *, required: bool = True) -> ObjectiveObligationV1:
    return ObjectiveObligationV1(objective_id="obj-1", description=description, required=required)


def test_full_coverage_passes() -> None:
    ob1 = _obligation("Group public methods by domain")
    ob2 = _obligation("Extract repeated DB/query boilerplate")
    step = _step(obligation_refs=(ob1.id, ob2.id))
    result = validate_obligation_coverage(obligations=(ob1, ob2), step_contracts=(step,))
    assert result.ok is True
    assert result.uncovered == ()


def test_dropped_obligation_is_reported_as_uncovered() -> None:
    """P3's own exit criterion: a planner that drops 'remove unused imports' from a
    five-part objective is rejected before execution."""
    ob1 = _obligation("Group public methods by domain")
    ob2 = _obligation("Extract repeated DB/query boilerplate")
    ob3 = _obligation("Remove unused imports")
    step = _step(obligation_refs=(ob1.id, ob2.id))  # ob3 silently dropped by the planner
    result = validate_obligation_coverage(obligations=(ob1, ob2, ob3), step_contracts=(step,))
    assert result.ok is False
    assert result.uncovered == (ob3,)


def test_non_required_obligations_do_not_need_coverage() -> None:
    required_ob = _obligation("Preserve public method names", required=True)
    optional_ob = _obligation("Nice-to-have cleanup", required=False)
    step = _step(obligation_refs=(required_ob.id,))
    result = validate_obligation_coverage(obligations=(required_ob, optional_ob), step_contracts=(step,))
    assert result.ok is True
    assert result.uncovered == ()


def test_coverage_is_the_union_across_multiple_step_contracts() -> None:
    ob1 = _obligation("A")
    ob2 = _obligation("B")
    step_a = _step(obligation_refs=(ob1.id,))
    step_b = _step(obligation_refs=(ob2.id,))
    result = validate_obligation_coverage(obligations=(ob1, ob2), step_contracts=(step_a, step_b))
    assert result.ok is True


def test_no_step_contracts_at_all_leaves_every_required_obligation_uncovered() -> None:
    ob1 = _obligation("A")
    result = validate_obligation_coverage(obligations=(ob1,), step_contracts=())
    assert result.ok is False
    assert result.uncovered == (ob1,)


def test_uncovered_preserves_obligation_order() -> None:
    ob1 = _obligation("first")
    ob2 = _obligation("second")
    ob3 = _obligation("third")
    result = validate_obligation_coverage(obligations=(ob1, ob2, ob3), step_contracts=())
    assert result.uncovered == (ob1, ob2, ob3)
