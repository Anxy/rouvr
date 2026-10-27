"""AIX-MPE-003 Packet MPE-24: autonomous-loop budgets and oscillation detection."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from backend.aix_core.runtime.objective.contracts import LoopBudgetV1


@dataclass
class AutonomousLoopTracker:
    budget: LoopBudgetV1 = field(default_factory=LoopBudgetV1)
    step_repairs: int = 0
    plan_revisions: int = 0
    reunderstand_rounds: int = 0
    total_attempts: int = 0
    tokens: int = 0
    patch_fingerprints: dict[str, int] = field(default_factory=dict)
    failure_fingerprints: dict[str, int] = field(default_factory=dict)
    plan_fingerprints: dict[str, int] = field(default_factory=dict)

    def record_attempt(self) -> str | None:
        self.total_attempts += 1
        if self.total_attempts > self.budget.max_total_attempts:
            return "MAX_TOTAL_ATTEMPTS"
        return None

    def record_repair(self) -> str | None:
        self.step_repairs += 1
        if self.step_repairs > self.budget.max_step_repairs:
            return "MAX_STEP_REPAIRS"
        return None

    def record_replan(self, plan_fingerprint: str) -> str | None:
        self.plan_revisions += 1
        self.plan_fingerprints[plan_fingerprint] = self.plan_fingerprints.get(plan_fingerprint, 0) + 1
        if self.plan_revisions > self.budget.max_plan_revisions:
            return "MAX_PLAN_REVISIONS"
        if self.plan_fingerprints[plan_fingerprint] >= 3:
            return "PLANNER_OSCILLATION"
        return None

    def record_reunderstand(self) -> str | None:
        self.reunderstand_rounds += 1
        if self.reunderstand_rounds > self.budget.max_reunderstand_rounds:
            return "MAX_REUNDERSTAND_ROUNDS"
        return None

    def record_patch(self, diff_text: str) -> str | None:
        if not str(diff_text or "").strip():
            return None
        digest = hashlib.sha256((diff_text or "").encode("utf-8")).hexdigest()[:16]
        self.patch_fingerprints[digest] = self.patch_fingerprints.get(digest, 0) + 1
        if self.patch_fingerprints[digest] >= 3:
            return "REPEATED_IDENTICAL_PATCH"
        return None

    def record_failure(self, reason: str) -> str | None:
        digest = hashlib.sha256((reason or "").encode("utf-8")).hexdigest()[:16]
        self.failure_fingerprints[digest] = self.failure_fingerprints.get(digest, 0) + 1
        if self.failure_fingerprints[digest] >= 3:
            return "REPEATED_IDENTICAL_FAILURE"
        return None


__all__ = ["AutonomousLoopTracker"]
