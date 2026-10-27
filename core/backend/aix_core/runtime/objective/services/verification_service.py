"""AIX Objective Verification Service.

Coordinates step-level semantic verification, contract coverage, obligation coverage,
and integration/objective proofs.

Authority Model:
Verifier -> VerificationResultV1 -> VerificationDecisionV1 -> TerminalizationGuard -> AixObjectiveCycleWorkflowEngine
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.verification.contract_coverage import (
    ContractCoverageResult,
    evaluate_contract_coverage,
)
from backend.aix_core.runtime.verification.integration_proof import (
    IntegrationProofV1,
    evaluate_integration_proof,
    evaluate_objective_proven,
)
from backend.aix_core.runtime.verification.objective_proof import (
    ObjectiveProofEvaluation,
    evaluate_objective_proof,
)
from backend.aix_core.runtime.verification.objective_truth import (
    CanonicalObjectiveTruthV1,
    derive_canonical_truth,
)
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    derive_ovc,
    evaluate_ovc_disposition_coverage,
)
from backend.aix_core.runtime.verification.obligation_coverage import (
    ObligationCoverageResult,
    validate_obligation_coverage,
)
from backend.aix_core.runtime.verification.semantic_verifier import (
    SemanticVerificationOutcome,
    verify_step,
)
from backend.aix_core.runtime.verification.gap import GapStore, GapV1
from backend.aix_core.runtime.verification.objective_verifier import ObjectiveVerifier
from backend.aix_core.runtime.verification.verification_decision import VerificationDecisionV1
from backend.aix_core.runtime.verification.verification_result import (
    VerificationResultV1,
)
from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.contracts import (
    ObjectiveEnvelopeV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    StepContractV1,
    StepRuntimeStateV1,
)

logger = logging.getLogger(__name__)


@dataclass
class StepVerificationSummary:
    step_id: str
    outcome: SemanticVerificationOutcome
    decision: VerificationDecisionV1 | None
    verification_result: VerificationResultV1 | None
    passed: bool


@dataclass
class ObjectiveVerificationOutcome:
    """Authoritative objective-scope verification at a workspace."""

    verification_result: VerificationResultV1
    verification_decision: VerificationDecisionV1
    new_gaps: list[GapV1]


class VerificationService:
    """Coordinates step and objective verification pipelines."""

    def __init__(self, gap_store: GapStore | None = None) -> None:
        self.gap_store = gap_store

    def verify_objective_at_workspace(
        self,
        *,
        ovc: ObjectiveVerificationContractV1,
        workspace_path: Path,
        gap_store: GapStore | None = None,
    ) -> ObjectiveVerificationOutcome:
        """Run canonical objective verification (VerificationResultV1 + VerificationDecisionV1)."""
        store = gap_store or self.gap_store
        verifier = ObjectiveVerifier()
        v_res, v_dec, new_gaps = verifier.verify_objective(
            ovc=ovc,
            workspace_path=workspace_path,
        )
        if store is not None:
            for gap in new_gaps:
                store.put_gap(gap)
        return ObjectiveVerificationOutcome(
            verification_result=v_res,
            verification_decision=v_dec,
            new_gaps=new_gaps,
        )

    def verify_step_execution(
        self,
        *,
        step_contract: StepContractV1,
        execute_result: dict[str, Any],
        workspace_path: Path | None = None,
        context: dict[str, Any] | None = None,
    ) -> StepVerificationSummary:
        """Runs semantic verification on a single executed step."""
        outcome = verify_step(
            step_contract=step_contract,
            execute_result=execute_result,
            workspace_path=workspace_path,
            context=context or {},
        )
        passed = bool(outcome.passed if hasattr(outcome, "passed") else outcome.ok)
        return StepVerificationSummary(
            step_id=step_contract.id,
            outcome=outcome,
            decision=getattr(outcome, "decision", None),
            verification_result=getattr(outcome, "verification_result", None),
            passed=passed,
        )

    def evaluate_contract_and_obligation_coverage(
        self,
        *,
        plan: PlanRevisionV1,
        obligations: list[ObjectiveObligationV1],
        ovc: ObjectiveVerificationContractV1 | None = None,
    ) -> tuple[ContractCoverageResult | None, ObligationCoverageResult]:
        """Validates that all plan steps and contracts provide complete coverage for required obligations."""
        contract_cov = (
            evaluate_contract_coverage(plan=plan, ovc=ovc) if ovc is not None else None
        )
        ob_cov = validate_obligation_coverage(plan=plan, obligations=obligations)
        return contract_cov, ob_cov

    def evaluate_proofs(
        self,
        *,
        all_mandatory_passed: bool,
        has_unresolved_regressions: bool = False,
        is_snapshot_stale: bool = False,
        has_invalid_evidence: bool = False,
        outcome_contract: OutcomeContractV1 | None = None,
        steps: list[StepContractV1] | None = None,
    ) -> tuple[ObjectiveProofEvaluation, IntegrationProofV1 | None]:
        """Evaluates objective proofs and integration proofs across completed steps."""
        obj_eval = evaluate_objective_proof(
            all_mandatory_criteria_passed=all_mandatory_passed,
            has_unresolved_regressions=has_unresolved_regressions,
            is_snapshot_stale=is_snapshot_stale,
            has_invalid_evidence=has_invalid_evidence,
        )
        integ_proof = None
        if outcome_contract is not None:
            integ_proof = evaluate_integration_proof(
                outcome_contract=outcome_contract,
                steps=tuple(steps or ()),
            )
        return obj_eval, integ_proof
