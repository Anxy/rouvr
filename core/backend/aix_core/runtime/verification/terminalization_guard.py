"""AIX Feature 12 (Packet 02) — Pure TerminalizationGuard.

Evaluates conjunctive gate for cycle terminalization.
Does NOT write cycle.phase directly. AixObjectiveCycleWorkflowEngine is sole phase writer.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)

TerminalizationDecisionKind = Literal[
    "ALLOW_EXECUTION_COMPLETE",
    "ALLOW_VERIFYING_OBJECTIVE",
    "ALLOW_CERTIFYING",
    "ALLOW_CERTIFIED",
    "DENY_TERMINALIZATION",
    "DENY_NEEDS_REPAIR",
]


class GuardsEvaluated(BaseModel):
    all_required_steps_verified: bool
    objective_runtime_verification: Literal["PASS", "FAIL", "NOT_RUN"]
    blocking_gaps: int
    required_evidence_complete: bool
    ovc_coverage_complete: bool

    @property
    def all_conjuncts_pass(self) -> bool:
        return (
            self.all_required_steps_verified
            and self.objective_runtime_verification == "PASS"
            and self.blocking_gaps == 0
            and self.required_evidence_complete
            and self.ovc_coverage_complete
        )


class TerminalizationDecisionV1(BaseModel):
    decision_id: str
    verification_decision_id: str
    objective_id: str
    decision: TerminalizationDecisionKind
    guards_evaluated: GuardsEvaluated
    reasoning: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def evaluate_terminalization(
    *,
    verification_decision: VerificationDecisionV1,
    guards: GuardsEvaluated,
    current_phase: str = "EXECUTING",
    decision_id: str = "",
) -> TerminalizationDecisionV1:
    """Pure evaluation of terminalization guard (Correction 3).
    
    Invariants:
    1. Step verifier PASS alone does NOT prove objective.
    2. Final gate is conjunction:
       all_required_steps_verified == True
       AND objective_runtime_verification == PASS
       AND blocking_gaps == 0
       AND required_evidence_complete == True
       AND ovc_coverage_complete == True
    """
    obj_id = verification_decision.objective_id
    dec_id = decision_id or f"term_{hashlib.md5((obj_id + verification_decision.decision_id + current_phase).encode()).hexdigest()[:12]}"

    # Check for blocking gaps or repair required
    if guards.blocking_gaps > 0 or verification_decision.decision in ("REPAIR", "HALT") or guards.objective_runtime_verification == "FAIL":
        return TerminalizationDecisionV1(
            decision_id=dec_id,
            verification_decision_id=verification_decision.decision_id,
            objective_id=obj_id,
            decision="DENY_NEEDS_REPAIR",
            guards_evaluated=guards,
            reasoning=(
                f"Terminalization denied due to defects/failures: blocking_gaps={guards.blocking_gaps}, "
                f"runtime_verification={guards.objective_runtime_verification}, "
                f"verifier_decision={verification_decision.decision}."
            ),
        )

    # Check full conjunction for certification
    if guards.all_conjuncts_pass and verification_decision.decision == "PASS":
        return TerminalizationDecisionV1(
            decision_id=dec_id,
            verification_decision_id=verification_decision.decision_id,
            objective_id=obj_id,
            decision="ALLOW_CERTIFIED",
            guards_evaluated=guards,
            reasoning="All 5 terminalization guard conjuncts passed with authoritative PASS.",
        )

    # Execution complete ready for objective verification
    if guards.all_required_steps_verified and guards.ovc_coverage_complete:
        if guards.objective_runtime_verification == "NOT_RUN":
            return TerminalizationDecisionV1(
                decision_id=dec_id,
                verification_decision_id=verification_decision.decision_id,
                objective_id=obj_id,
                decision="ALLOW_VERIFYING_OBJECTIVE",
                guards_evaluated=guards,
                reasoning="Plan steps execution complete; transitioning to independent runtime objective verification.",
            )
        return TerminalizationDecisionV1(
            decision_id=dec_id,
            verification_decision_id=verification_decision.decision_id,
            objective_id=obj_id,
            decision="ALLOW_EXECUTION_COMPLETE",
            guards_evaluated=guards,
            reasoning="All required plan steps verified; awaiting objective certification.",
        )

    # Default deny
    reasons = []
    if not guards.all_required_steps_verified:
        reasons.append("not all required steps verified")
    if not guards.required_evidence_complete:
        reasons.append("required evidence incomplete")
    if not guards.ovc_coverage_complete:
        reasons.append("OVC disposition coverage incomplete")

    return TerminalizationDecisionV1(
        decision_id=dec_id,
        verification_decision_id=verification_decision.decision_id,
        objective_id=obj_id,
        decision="DENY_TERMINALIZATION",
        guards_evaluated=guards,
        reasoning=f"Terminalization denied: {'; '.join(reasons)}.",
    )


__all__ = [
    "GuardsEvaluated",
    "TerminalizationDecisionKind",
    "TerminalizationDecisionV1",
    "evaluate_terminalization",
]
