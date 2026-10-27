import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.verification.semantic_verifier import verify_step


def _proposal_result() -> RealExecuteResult:
    return RealExecuteResult(
        ok=True,
        code="PROPOSAL_READY",
        workspace="ws",
        evidence_refs=[],
        workhub_loaded=False,
        artifacts={"proposed_markdown": "proposed/nursery-good-habits-poems.md"},
        execution_status="NOT_STARTED",
        verification_status="NOT_RUN",
        objective_status="AWAITING_REVIEW",
        real_operations=0,
        target_files_modified=0,
        administrative_artifacts_generated=1,
    )


@pytest.mark.asyncio
async def test_proposal_only_docs_pass_when_semantic_verifier_enabled() -> None:
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t",
        step_index=0,
        purpose="create nursery poems markdown",
        target_scope=("README.md",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("markdown poems exist",),
        validation_commands=("echo proposal",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        status="PENDING",
    )
    outcome = await verify_step(
        result=_proposal_result(),
        step_contract=contract,
        repair_budget_remaining=2,
        semantic_verifier_enabled=True,
    )
    assert outcome.verdict == "PASS"
    assert outcome.mechanical_passed is True
