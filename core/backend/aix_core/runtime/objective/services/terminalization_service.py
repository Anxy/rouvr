"""AIX Objective Terminalization Service.

Enforces the strict authority model:
Verifier -> VerificationResultV1 -> VerificationDecisionV1 -> TerminalizationGuard -> AixObjectiveCycleWorkflowEngine -> Phase Transition

Invariants:
1. AixObjectiveCycleWorkflowEngine is the sole phase writer.
2. Terminalization is blocked unless all conjunction guards pass.
3. No direct backend phase writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.aix_core.runtime.verification.gap import GapStore
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    evaluate_ovc_disposition_coverage,
)
from backend.aix_core.runtime.verification.terminalization_guard import (
    GuardsEvaluated,
    TerminalizationDecisionV1,
    evaluate_terminalization,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.aix_core.runtime.objective.step_scheduler import (
    all_required_steps_passed,
)

if TYPE_CHECKING:
    from backend.workflows.temporal.objective_cycle_workflow import (
        AixObjectiveCycleWorkflowEngine,
    )
    from backend.aix_core.runtime.objective.contracts import PlanRevisionV1, StepRuntimeStateV1

logger = logging.getLogger(__name__)


@dataclass
class TerminalizationEvaluationResult:
    terminalization_decision: TerminalizationDecisionV1
    verification_decision: VerificationDecisionV1
    guards_evaluated: GuardsEvaluated
    all_steps_verified: bool
    blocking_gaps_count: int


class TerminalizationService:
    """Evaluates conjunctive terminalization guards and coordinates phase transitions
    strictly through AixObjectiveCycleWorkflowEngine.
    """

    def __init__(self, gap_store: GapStore | None = None) -> None:
        self.gap_store = gap_store or GapStore()

    def evaluate_guards(
        self,
        *,
        objective_id: str,
        cycle_id: str,
        version: int,
        current_phase: str,
        plan: PlanRevisionV1 | None,
        step_runtime: dict[str, StepRuntimeStateV1] | None,
        ovc: ObjectiveVerificationContractV1 | None,
        real_execute: dict[str, Any] | None = None,
        workspace_path: Path | None = None,
    ) -> TerminalizationEvaluationResult:
        """Pure evaluation of all terminalization conjunctions.
        Does NOT mutate engine phase or cycle state.
        """
        runtime = step_runtime or {}
        all_steps_verified = bool(
            plan is not None and runtime and all_required_steps_passed(plan, runtime)
        )

        disp_res = (
            evaluate_ovc_disposition_coverage(ovc, plan.steps)
            if (ovc is not None and plan is not None)
            else None
        )
        ovc_cov_complete = bool(disp_res and disp_res.ok)

        re = real_execute or {}
        evidence_complete = bool(
            re.get("evidence") or re.get("evidence_refs") or re.get("ok") or all_steps_verified
        )

        # Objective verifier evaluation when OVC exists
        if ovc is not None and (workspace_path is not None or all_steps_verified):
            from backend.aix_core.runtime.verification.objective_verifier import ObjectiveVerifier

            verifier = ObjectiveVerifier()
            obj_res, obj_dec, gaps = verifier.verify_objective(
                ovc=ovc,
                workspace_path=workspace_path or Path.cwd(),
            )
            for g in gaps:
                self.gap_store.put_gap(g)
            runtime_proof_status = obj_res.runtime_proof_status
            verif_dec = obj_dec
            blocking_gaps_count = self.gap_store.count_blocking_gaps(objective_id)
        else:
            obj_status = str(re.get("objective_status") or "").upper()
            runtime_proof_status = "NOT_RUN"
            if obj_status == "COMPLETED" or re.get("runtime_verified") is True:
                runtime_proof_status = "PASS"
            elif obj_status in ("FAILED", "BLOCKED"):
                runtime_proof_status = "FAIL"

            verif_dec_kind = (
                "PASS"
                if all_steps_verified and (runtime_proof_status != "FAIL")
                else "REPAIR"
            )
            verif_dec = VerificationDecisionV1(
                decision_id=f"vdec_{cycle_id}_{version}",
                verification_id=f"v_{cycle_id}_{version}",
                objective_id=objective_id,
                decision=verif_dec_kind,
                reasoning="Evaluated cycle terminalization criteria.",
                blocking_gaps=[],
            )
            blocking_gaps_count = 0

        guards = GuardsEvaluated(
            all_required_steps_verified=all_steps_verified,
            objective_runtime_verification=runtime_proof_status,  # type: ignore[arg-type]
            blocking_gaps=blocking_gaps_count,
            required_evidence_complete=evidence_complete,
            ovc_coverage_complete=ovc_cov_complete,
        )

        term_dec = evaluate_terminalization(
            verification_decision=verif_dec,
            guards=guards,
            current_phase=current_phase,
        )

        return TerminalizationEvaluationResult(
            terminalization_decision=term_dec,
            verification_decision=verif_dec,
            guards_evaluated=guards,
            all_steps_verified=all_steps_verified,
            blocking_gaps_count=blocking_gaps_count,
        )

    def apply_terminalization_to_engine(
        self,
        *,
        engine: AixObjectiveCycleWorkflowEngine,
        step_id: str,
        evaluation: TerminalizationEvaluationResult,
    ) -> TerminalizationDecisionV1:
        """Sole terminalization phase transition path (Invariant 1).
        Instructs the engine to transition according to the evaluated TerminalizationDecision.
        """
        engine.continue_after_approval(
            step_id=step_id,
            decision=evaluation.terminalization_decision,
            verification_decision=evaluation.verification_decision,
        )
        return evaluation.terminalization_decision

    def evaluate_from_reverify_outcome(
        self,
        *,
        verification_decision: VerificationDecisionV1,
        runtime_proof_status: str,
        blocking_gaps: int,
        current_phase: str,
        all_steps_verified: bool = True,
        required_evidence_complete: bool = True,
        ovc_coverage_complete: bool = True,
    ) -> TerminalizationEvaluationResult:
        """Evaluate terminalization using authoritative post-reverify verification outcome.

        Used when workspace objective verification already ran; avoids re-evaluating from
        stale ``real_execute`` evidence flags.
        """
        guards = GuardsEvaluated(
            all_required_steps_verified=all_steps_verified,
            objective_runtime_verification=runtime_proof_status,  # type: ignore[arg-type]
            blocking_gaps=blocking_gaps,
            required_evidence_complete=required_evidence_complete,
            ovc_coverage_complete=ovc_coverage_complete,
        )
        term_dec = evaluate_terminalization(
            verification_decision=verification_decision,
            guards=guards,
            current_phase=current_phase,
        )
        return TerminalizationEvaluationResult(
            terminalization_decision=term_dec,
            verification_decision=verification_decision,
            guards_evaluated=guards,
            all_steps_verified=all_steps_verified,
            blocking_gaps_count=blocking_gaps,
        )
