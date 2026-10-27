"""AIX Feature 12 (Packet 11) — Cost Governance & Accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from backend.aix_core.runtime.model_registry.model_profile import ModelProfileV1

AutonomousBudgetVerdict = Literal["OK", "AUTONOMOUS_BLOCKED", "REQUIRES_HUMAN_DECISION"]


@dataclass(frozen=True)
class CostEntryV1:
    entry_id: str
    objective_id: str
    cycle_id: str
    role: str
    model_id: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    gap_id: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class CostBudgetExceededError(ValueError):
    pass


class CostLedger:
    """Tracks and bounds AI execution spending per objective and role."""

    def __init__(
        self,
        *,
        max_objective_cost_usd: float = 5.0,
        max_repair_attempts: int = 3,
        max_repair_cost_usd: float = 1.0,
    ) -> None:
        self._entries: list[CostEntryV1] = []
        self.max_objective_cost_usd = max_objective_cost_usd
        self.max_repair_attempts = max_repair_attempts
        self.max_repair_cost_usd = max_repair_cost_usd

    def record_usage(
        self,
        *,
        objective_id: str,
        cycle_id: str,
        role: str,
        model: ModelProfileV1,
        tokens_in: int,
        tokens_out: int,
        gap_id: str | None = None,
    ) -> CostEntryV1:
        cost_in = (tokens_in / 1_000_000.0) * model.input_cost_per_M
        cost_out = (tokens_out / 1_000_000.0) * model.output_cost_per_M
        total_cost = round(cost_in + cost_out, 6)

        entry = CostEntryV1(
            entry_id=f"cost_{len(self._entries) + 1}",
            objective_id=objective_id,
            cycle_id=cycle_id,
            role=role,
            model_id=model.model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=total_cost,
            gap_id=gap_id,
        )
        self._entries.append(entry)
        return entry

    def get_objective_total_cost(self, objective_id: str) -> float:
        return round(sum(e.cost_usd for e in self._entries if e.objective_id == objective_id), 6)

    def get_role_cost(self, objective_id: str, role: str) -> float:
        return round(
            sum(e.cost_usd for e in self._entries if e.objective_id == objective_id and e.role == role),
            6,
        )

    def get_gap_repair_cost(self, gap_id: str) -> float:
        return round(sum(e.cost_usd for e in self._entries if e.gap_id == gap_id), 6)

    def check_budget_headroom(
        self,
        *,
        objective_id: str,
        budget_limit_usd: float,
    ) -> tuple[bool, float, float]:
        current_spend = self.get_objective_total_cost(objective_id)
        remaining = round(budget_limit_usd - current_spend, 6)
        return remaining > 0, current_spend, max(0.0, remaining)

    def enforce_autonomous_budget(
        self,
        *,
        objective_id: str,
        repair_attempt_count: int = 0,
        gap_id: str | None = None,
    ) -> tuple[AutonomousBudgetVerdict, str]:
        total = self.get_objective_total_cost(objective_id)
        if total >= self.max_objective_cost_usd:
            return (
                "REQUIRES_HUMAN_DECISION",
                f"objective cost ${total:.4f} exceeds max ${self.max_objective_cost_usd:.2f}",
            )
        if repair_attempt_count >= self.max_repair_attempts:
            return (
                "AUTONOMOUS_BLOCKED",
                f"repair attempts {repair_attempt_count} >= max {self.max_repair_attempts}",
            )
        if gap_id is not None:
            repair_cost = self.get_gap_repair_cost(gap_id)
            if repair_cost >= self.max_repair_cost_usd:
                return (
                    "AUTONOMOUS_BLOCKED",
                    f"gap repair cost ${repair_cost:.4f} exceeds max ${self.max_repair_cost_usd:.2f}",
                )
        return "OK", ""

    def has_budget_for_repair(
        self,
        *,
        objective_id: str,
        repair_attempt_count: int = 0,
        gap_id: str | None = None,
    ) -> tuple[bool, AutonomousBudgetVerdict, str]:
        """Return (allowed, verdict, reason) for autonomous repair continuation."""
        verdict, reason = self.enforce_autonomous_budget(
            objective_id=objective_id,
            repair_attempt_count=repair_attempt_count,
            gap_id=gap_id,
        )
        return verdict == "OK", verdict, reason


__all__ = [
    "AutonomousBudgetVerdict",
    "CostEntryV1",
    "CostBudgetExceededError",
    "CostLedger",
]
