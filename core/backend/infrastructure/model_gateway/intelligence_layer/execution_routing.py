"""W07/W08: Execution + verification role routing via ModelRegistry + GPR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.infrastructure.model_gateway.intelligence_layer.metrics import intelligence_metrics
from backend.infrastructure.model_gateway.intelligence_layer.model_registry import (
    IntelligenceRole,
    load_model_registry,
)
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
)

VerificationDisposition = Literal["PASS", "FAIL", "REPLAN"]


@dataclass(frozen=True)
class RoleRouteV1:
    role: IntelligenceRole
    model_ref: str
    decision: RouteDecisionV1


def resolve_intelligence_role(
    role: IntelligenceRole,
    *,
    router: GovernedProviderRouter | None = None,
    registry=None,
) -> RoleRouteV1:
    """Resolve a governed Integration route for conversation/planner/executor/verifier."""
    reg = registry or load_model_registry()
    model_ref = reg.model_ref_for(role)
    gpr = router or GovernedProviderRouter()
    decision = gpr.resolve(role=role, model_ref=model_ref)
    if role == "executor":
        intelligence_metrics.execution_route_count += 1
    elif role == "verifier":
        intelligence_metrics.verification_route_count += 1
    return RoleRouteV1(role=role, model_ref=model_ref, decision=decision)


def assert_independent_verification(
    *,
    executor_model_ref: str,
    verifier_model_ref: str,
) -> None:
    """Fail closed if verifier would be the same model identity as executor (self-verify)."""
    if (
        executor_model_ref.strip()
        and verifier_model_ref.strip()
        and executor_model_ref.strip() == verifier_model_ref.strip()
    ):
        raise ValueError("SELF_VERIFICATION_FORBIDDEN")


def map_verification_outcome(status: str) -> VerificationDisposition:
    normalized = str(status or "").strip().upper()
    if normalized in {"PASS", "PASSED", "OK", "SUCCESS"}:
        return "PASS"
    if normalized in {"REPLAN", "REPLAN_REQUIRED", "NEEDS_REPLAN"}:
        return "REPLAN"
    return "FAIL"
