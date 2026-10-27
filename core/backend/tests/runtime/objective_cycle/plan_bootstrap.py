"""Shared ACCEPTED-plan bootstrap for tests that exercise execute, not planner authority."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def seed_accepted_bootstrap_contract(
    backend: DurableObjectiveCycleApiBackend,
    objective_id: str,
    cycle_id: str,
    *,
    target: str = "helper.py",
    schema_ref: str = "aix.step_contract.v1.generic_edit",
    purpose: str = "seeded execute bootstrap",
    validation_commands: tuple[str, ...] = ("true; use-repository-default-tests",),
) -> None:
    """Give tests an ACCEPTED PlanRevision via the seeded-step-contract wrap.

    AIX-PA-002 forbids execute without an accepted plan. Tests that fake
    `run_real_execute` to prove completion/verifier/WorkHub behavior must seed
    a contract; `_ensure_accepted_plan_for_seeded_contract` then wraps it.
    """
    if objective_id in backend._step_contracts:  # noqa: SLF001
        return
    # Prefer the human purpose as acceptance so text-replace objectives stay
    # mechanically decidable (contain/replace markers). Empty obligation_refs —
    # copying admit obligations into the seed made PRODUCT_STRUCTURE_COVERAGE
    # fail with "obligation_refs present but no derived tokens".
    backend._step_contracts[objective_id] = StepContractV1(  # noqa: SLF001
        cycle_id=cycle_id,
        tenant_id="tenant-1",
        step_index=0,
        purpose=purpose,
        target_scope=(target,),
        expected_output_schema_ref=schema_ref,
        acceptance_criteria=(purpose or "seeded step complete",),
        validation_commands=validation_commands,
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        obligation_refs=(),
        status="PENDING",
    )
