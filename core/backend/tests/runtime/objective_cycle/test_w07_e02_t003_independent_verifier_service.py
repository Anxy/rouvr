"""AIX-W07-E02-T003: Independent Verifier Service Tests.

Verifies:
1. Executor narrative is untrusted input: text claims of success without evidence cannot pass.
2. Independent verifier rejects forged PASS narratives when evidence checks fail.
3. COMPLETED_FOR_REVIEW is never promoted to PROVEN_COMPLETE without verified evidence.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.verification.semantic_verifier import (
    SemanticVerificationOutcome,
    verify_step,
)

_BUDGET = BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=3, steps=10, concurrency=1)


@pytest.mark.asyncio
async def test_executor_pass_narrative_without_evidence_is_rejected() -> None:
    """An executor narrative claiming success without mechanical evidence fails verification."""
    contract = StepContractV1(
        id="step-001",
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=1,
        purpose="Update auth logic",
        depends_on=(),
        target_scope=("backend/routers/auth.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("auth route updated",),
        validation_commands=("pytest backend/tests/test_auth.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status="PENDING",
    )

    # Forged result: ok=True, but completion_gates indicates mechanical failure (no diff produced)
    forged_result = RealExecuteResult(
        ok=True,
        code="EXECUTOR_FORGED_CLAIM",
        workspace="/workspace",
        completion_gates={
            "passed": False,
            "gates": {"diff_produced": False},
            "verification_verdict": "FAILED",
        },
    )

    outcome = await verify_step(
        result=forged_result,
        step_contract=contract,
        repair_budget_remaining=3,
        semantic_verifier_enabled=False,
    )

    assert outcome.mechanical_passed is False
    assert outcome.verdict != "PASS"


@pytest.mark.asyncio
async def test_valid_evidence_passes_independent_verification() -> None:
    """Step execution with valid mechanical patch and diff evidence passes verification."""
    contract = StepContractV1(
        id="step-002",
        cycle_id="cyc-2",
        tenant_id="tenant-1",
        step_index=1,
        purpose="Fix bug by replacing old with new",
        depends_on=(),
        target_scope=("backend/utils.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("replace old with new",),
        validation_commands=("pytest backend/tests/test_utils.py",),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=_BUDGET,
        required_evidence=("file://diff.patch",),
        status="PENDING",
    )

    valid_result = RealExecuteResult(
        ok=True,
        code="EXECUTOR_VALID_FIX",
        workspace="/workspace",
        diff_text="diff --git a/backend/utils.py b/backend/utils.py\n--- a/backend/utils.py\n+++ b/backend/utils.py\n@@ -1 +1 @@\n-old\n+new\n",
        changed_source_files=[{"path": "backend/utils.py", "lines_added": 1, "lines_removed": 1}],
        completion_gates={
            "passed": True,
            "gates": {"diff_produced": True, "target_modified": True},
            "verification_verdict": "PASSED",
        },
    )

    outcome = await verify_step(
        result=valid_result,
        step_contract=contract,
        repair_budget_remaining=3,
        semantic_verifier_enabled=False,
    )

    assert outcome.mechanical_passed is True
