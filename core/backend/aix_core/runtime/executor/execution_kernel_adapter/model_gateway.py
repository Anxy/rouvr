"""EX4-R2-P4-S001: thin Execution Kernel adapter to Hyeve model routing.

The authoritative selection algorithm remains services.model_routing_policy_engine.
This module only translates a kernel dispatch request into that existing contract.
"""

from __future__ import annotations

from typing import Any

from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.services.model_routing_policy_engine import resolve_model_route


def _resolve_role(
    role: str,
    *,
    task_type: str,
    context: dict[str, Any],
    preferred_provider: str | None = None,
) -> dict[str, Any]:
    decision = resolve_model_route(
        task_type=task_type,
        risk_level=str(context.get("risk_level") or "medium"),
        model_policy=context.get("model_policy") or "allow_escalate",
        project_id=context.get("project_id"),
        policies=list(context.get("policies") or []),
        bindings=list(context.get("bindings") or []),
        health_rows=list(context.get("health_rows") or []),
        escalation_policies=list(context.get("escalation_policies") or []),
        retry_count=int(context.get("retry_count") or 0),
        preferred_provider=preferred_provider,
        force_escalation=bool(context.get("force_escalation", False)),
    )
    payload = decision.to_payload()
    payload["role"] = role
    payload["gateway"] = "aix_model_gateway"
    if decision.decision_status == "blocked":
        raise ExecutionKernelRepositoryError(
            f"model gateway blocked {role}: {decision.decision_reason or 'UNKNOWN'}"
        )
    return payload


def resolve_kernel_model_routes(
    *,
    routing_context: dict[str, Any],
    include_verifier: bool = False,
    require_independent_verifier: bool = False,
) -> dict[str, dict[str, Any]]:
    """Resolve deterministic executor/verifier routes from Hyeve-owned snapshots."""
    if not isinstance(routing_context, dict) or not routing_context:
        raise ExecutionKernelRepositoryError("model routing context is required")
    executor = _resolve_role(
        "executor",
        task_type=str(routing_context.get("task_type") or "code_change"),
        context=routing_context,
        preferred_provider=routing_context.get("preferred_provider"),
    )
    routes = {"executor": executor}
    if include_verifier:
        verifier = _resolve_role(
            "verifier",
            task_type=str(routing_context.get("verifier_task_type") or "verification"),
            context=routing_context,
            preferred_provider=routing_context.get("verifier_preferred_provider"),
        )
        if require_independent_verifier and (
            executor.get("selected_provider"),
            executor.get("selected_model_id"),
        ) == (
            verifier.get("selected_provider"),
            verifier.get("selected_model_id"),
        ):
            raise ExecutionKernelRepositoryError("SELF_VERIFICATION_FORBIDDEN")
        routes["verifier"] = verifier
    return routes


__all__ = ["resolve_kernel_model_routes"]
