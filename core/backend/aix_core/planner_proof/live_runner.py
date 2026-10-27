"""Production-path planner proof runner (uses propose_step_contract)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.planner.plan_quality import evaluate_plan_quality
from backend.aix_core.runtime.planner.step_contract_planner import (
    StepContractProposalV1,
    propose_step_contract,
)
from backend.aix_core.planner_proof.fallback_policy import FallbackDecision, apply_fallback_policy
from backend.aix_core.planner_proof.fixtures import PlannerProofFixture, representative_multi_obligation_fixture
from backend.aix_core.planner_proof.provenance import evidence_bundle


@dataclass(frozen=True)
class PlannerProofResult:
    proposal: StepContractProposalV1
    quality_ok: bool
    fallback: FallbackDecision
    evidence: dict[str, Any]


async def run_planner_proof(
    *,
    fixture: PlannerProofFixture | None = None,
    actor_id: str = "proof-actor",
    authorization_header: str = "Bearer proof",
    timeout_seconds: float = 5.0,
    retry_count: int = 0,
) -> PlannerProofResult:
    """Call the production planner entrypoint and attach cost/evidence + fallback."""
    fx = fixture or representative_multi_obligation_fixture()
    proposal = await propose_step_contract(
        objective=fx.objective,
        definition_of_done=fx.definition_of_done,
        understanding=fx.understanding,
        constraints=fx.constraints,
        tenant_id=fx.tenant_id,
        cycle_id=fx.cycle_id,
        step_index=0,
        assigned_role="executor",
        step_budget=fx.budget,
        actor_id=actor_id,
        authorization_header=authorization_header,
        timeout_seconds=timeout_seconds,
        obligations=fx.obligations,
        objective_id=fx.objective_id,
    )
    quality_ok = False
    if proposal.plan is not None:
        q = evaluate_plan_quality(plan=proposal.plan, obligations=fx.obligations)
        quality_ok = bool(q.ok)
    plan_ok = proposal.plan is not None and proposal.reason is None and quality_ok
    fallback = apply_fallback_policy(
        reason=proposal.reason,
        plan_ok=plan_ok,
        retry_count=retry_count,
    )
    step_count = len(proposal.plan.steps) if proposal.plan is not None else 0
    evidence = evidence_bundle(
        provenance=proposal.provenance,
        plan_step_count=step_count,
        reason=proposal.reason,
        quality_ok=quality_ok,
        fallback={
            "policy_id": fallback.policy_id,
            "disposition": fallback.disposition,
            "reason_code": fallback.reason_code,
            "allow_synthetic_plan": fallback.allow_synthetic_plan,
        },
    )
    return PlannerProofResult(
        proposal=proposal,
        quality_ok=quality_ok,
        fallback=fallback,
        evidence=evidence,
    )
