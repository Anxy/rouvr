"""P25: Governed Self-Improvement Lane.

Pipeline: baseline → isolated workspace → improvement objective → governed execution
→ tests → P23 evaluation → baseline comparison → security/policy checks
→ candidate → approval/promotion → rollback.

Critical invariant: AIX cannot weaken its own governance/evaluation/promotion
requirements to pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.aix_core.runtime.evaluation.evaluation_engine import (
    BaselineComparison,
    BaselineV1,
    EvaluationEngine,
)

SelfImprovementStage = Literal[
    "baseline", "workspace_created", "improvement_planned",
    "executing", "testing", "evaluating", "comparing",
    "security_check", "candidate", "pending_approval",
    "approved", "promoted", "rolled_back", "rejected",
]


@dataclass
class SelfImprovementCandidateV1:
    candidate_id: str
    objective: str
    stage: SelfImprovementStage = "baseline"
    baseline_eval_run_id: str = ""
    candidate_eval_run_id: str = ""
    comparison: BaselineComparison | None = None
    tests_passed: bool = False
    security_passed: bool = False
    governance_intact: bool = True
    approved_by: str | None = None
    rejected_reason: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


class SelfImprovementLane:
    """Governed self-improvement pipeline."""

    PROTECTED_MODULES = frozenset({
        "policy_gateway", "policy_store", "capability_registry",
        "authorization", "audit", "evaluation", "self_improvement",
        "recovery_manager", "budget_ledger",
    })

    def __init__(self, evaluation_engine: EvaluationEngine) -> None:
        self._eval = evaluation_engine
        self._candidates: dict[str, SelfImprovementCandidateV1] = {}

    def create_candidate(self, objective: str, *, baseline_run_id: str) -> SelfImprovementCandidateV1:
        baseline = self._eval.get_run(baseline_run_id)
        if baseline is None:
            raise ValueError(f"baseline run {baseline_run_id!r} not found — cannot improve without baseline")
        cid = f"si-{uuid.uuid4().hex[:12]}"
        candidate = SelfImprovementCandidateV1(
            candidate_id=cid, objective=objective,
            baseline_eval_run_id=baseline_run_id,
            stage="workspace_created",
        )
        self._candidates[cid] = candidate
        return candidate

    def record_tests(self, candidate_id: str, *, passed: bool) -> None:
        c = self._candidates.get(candidate_id)
        if c is None:
            raise ValueError("candidate not found")
        c.tests_passed = passed
        c.stage = "testing"

    def record_evaluation(self, candidate_id: str, *, eval_run_id: str) -> None:
        c = self._candidates.get(candidate_id)
        if c is None:
            raise ValueError("candidate not found")
        c.candidate_eval_run_id = eval_run_id
        c.stage = "evaluating"

    def compare_to_baseline(self, candidate_id: str) -> BaselineComparison | None:
        c = self._candidates.get(candidate_id)
        if c is None:
            return None
        baseline_run = self._eval.get_run(c.baseline_eval_run_id)
        candidate_run = self._eval.get_run(c.candidate_eval_run_id)
        if baseline_run is None or candidate_run is None:
            return None
        baseline_metrics = self._eval.aggregate_metrics(baseline_run)
        bl = BaselineV1(baseline_id="temp", version="baseline", created_at="", metrics=baseline_metrics)
        comparison = self._eval.compare_to_baseline(candidate_run, bl)
        c.comparison = comparison
        c.stage = "comparing"
        return comparison

    def security_check(self, candidate_id: str, *, modified_files: list[str]) -> bool:
        c = self._candidates.get(candidate_id)
        if c is None:
            return False
        for f in modified_files:
            for protected in self.PROTECTED_MODULES:
                if protected in f.lower():
                    c.governance_intact = False
                    c.security_passed = False
                    c.stage = "security_check"
                    return False
        c.security_passed = True
        c.stage = "candidate"
        return True

    def request_approval(self, candidate_id: str) -> SelfImprovementCandidateV1 | None:
        c = self._candidates.get(candidate_id)
        if c is None:
            return None
        if not c.tests_passed:
            c.rejected_reason = "tests did not pass"
            c.stage = "rejected"
            return c
        if not c.security_passed:
            c.rejected_reason = "security check failed — governance modules modified"
            c.stage = "rejected"
            return c
        if not c.governance_intact:
            c.rejected_reason = "governance integrity violated"
            c.stage = "rejected"
            return c
        c.stage = "pending_approval"
        return c

    def approve(self, candidate_id: str, *, approved_by: str) -> bool:
        c = self._candidates.get(candidate_id)
        if c is None or c.stage != "pending_approval":
            return False
        c.approved_by = approved_by
        c.stage = "approved"
        return True

    def promote(self, candidate_id: str) -> bool:
        c = self._candidates.get(candidate_id)
        if c is None or c.stage != "approved":
            return False
        c.stage = "promoted"
        return True

    def rollback(self, candidate_id: str) -> bool:
        c = self._candidates.get(candidate_id)
        if c is None:
            return False
        c.stage = "rolled_back"
        return True

    def get(self, candidate_id: str) -> SelfImprovementCandidateV1 | None:
        return self._candidates.get(candidate_id)


__all__ = [
    "SelfImprovementStage", "SelfImprovementCandidateV1", "SelfImprovementLane",
]
