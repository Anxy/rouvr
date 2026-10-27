"""AIX Model Registry — RoleModelPolicyV1 and canonical role kinds."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentRoleKind = Literal[
    "objective_understanding",
    "planner",
    "complex_planner",
    "executor",
    "bulk_executor",
    "tester",
    "verifier",
    "critical_reviewer",
]

ComplexityKind = Literal["LOW", "NORMAL", "HIGH"]
RiskKind = Literal["NORMAL", "HIGH", "ESCALATION"]


class RoleModelPolicyV1(BaseModel):
    role: AgentRoleKind
    primary_model_id: str
    fallback_model_ids: tuple[str, ...] = ()
    max_attempts: int = Field(default=2, ge=1)
    cost_budget_usd: float = Field(default=0.50, ge=0.0)
    require_different_family_from: tuple[str, ...] = ()
    escalation_only: bool = False
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: float = 60.0


__all__ = [
    "AgentRoleKind",
    "ComplexityKind",
    "RiskKind",
    "RoleModelPolicyV1",
]
