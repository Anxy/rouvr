"""EX5-S2: Model Execution Integrity — route contract, enforce, switch, audit.

Selected model routes must actually drive executor/verifier behaviour.
Provider-neutral: AIX stores and enforces (provider, model_id); Hyeve gateway
remains the selection authority (ADR-015).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


@dataclass(frozen=True)
class RoleRoute:
    role: str  # executor | verifier
    provider: str
    model_id: str
    decision_status: str = "selected"
    decision_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRouteContract:
    """Persisted binding of selected models to a work item / cycle."""

    route_id: str
    tenant_id: str
    work_item_id: str
    executor: RoleRoute
    verifier: RoleRoute | None = None
    status: str = "ACTIVE"  # ACTIVE | SUPERSEDED | FALLBACK
    bound_at: str = ""
    cycle_id: str | None = None
    superseded_by: str | None = None
    switch_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "tenant_id": self.tenant_id,
            "work_item_id": self.work_item_id,
            "status": self.status,
            "bound_at": self.bound_at,
            "cycle_id": self.cycle_id,
            "superseded_by": self.superseded_by,
            "switch_reason": self.switch_reason,
            "executor": role_route_to_dict(self.executor),
            "verifier": role_route_to_dict(self.verifier) if self.verifier else None,
        }


@dataclass
class ModelExecutionContext:
    """Runtime context that executor/verifier must honour."""

    route: ExecutionRouteContract
    actual_executor_provider: str | None = None
    actual_executor_model_id: str | None = None
    actual_verifier_provider: str | None = None
    actual_verifier_model_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.to_dict(),
            "actual_executor_provider": self.actual_executor_provider,
            "actual_executor_model_id": self.actual_executor_model_id,
            "actual_verifier_provider": self.actual_verifier_provider,
            "actual_verifier_model_id": self.actual_verifier_model_id,
        }


class ProviderNeutralExecutor(Protocol):
    """Provider-neutral execution interface — implementations must honour context.route."""

    async def execute(
        self, *, context: ModelExecutionContext, payload: dict[str, Any]
    ) -> dict[str, Any]: ...


def role_route_to_dict(route: RoleRoute) -> dict[str, Any]:
    return {
        "role": route.role,
        "provider": route.provider,
        "model_id": route.model_id,
        "decision_status": route.decision_status,
        "decision_reason": route.decision_reason,
        "raw": dict(route.raw),
    }


def role_route_from_gateway(role: str, payload: dict[str, Any]) -> RoleRoute:
    provider = str(
        payload.get("selected_provider")
        or payload.get("provider")
        or ""
    ).strip()
    model_id = str(
        payload.get("selected_model_id")
        or payload.get("model_id")
        or payload.get("selected_model_name")
        or ""
    ).strip()
    if not provider or not model_id:
        raise ExecutionKernelRepositoryError(
            f"incomplete {role} route: provider and model_id are required"
        )
    return RoleRoute(
        role=role,
        provider=provider,
        model_id=model_id,
        decision_status=str(payload.get("decision_status") or "selected"),
        decision_reason=payload.get("decision_reason"),
        raw=dict(payload),
    )


def contract_from_model_routes(
    *,
    tenant_id: str,
    work_item_id: str,
    model_routes: dict[str, Any],
    cycle_id: str | None = None,
    route_id: str | None = None,
    status: str = "ACTIVE",
    switch_reason: str | None = None,
) -> ExecutionRouteContract:
    tenant_id = _require_tenant(tenant_id)
    work_item_id = str(work_item_id or "").strip()
    if not work_item_id:
        raise ExecutionKernelRepositoryError("work_item_id is required")
    if not isinstance(model_routes, dict) or "executor" not in model_routes:
        raise ExecutionKernelRepositoryError("model_routes.executor is required")
    executor = role_route_from_gateway("executor", dict(model_routes["executor"]))
    verifier = None
    if model_routes.get("verifier"):
        verifier = role_route_from_gateway("verifier", dict(model_routes["verifier"]))
    return ExecutionRouteContract(
        route_id=str(route_id or f"rt_{new_ulid()[:12]}"),
        tenant_id=tenant_id,
        work_item_id=work_item_id,
        executor=executor,
        verifier=verifier,
        status=status,
        bound_at=_utc_now(),
        cycle_id=cycle_id,
        switch_reason=switch_reason,
    )


def enforce_executor_model(
    context: ModelExecutionContext,
    *,
    provider: str,
    model_id: str,
) -> ModelExecutionContext:
    """Fail closed if the executing model is not the bound executor."""
    provider = str(provider or "").strip()
    model_id = str(model_id or "").strip()
    expected = context.route.executor
    if (provider, model_id) != (expected.provider, expected.model_id):
        raise ExecutionKernelRepositoryError(
            f"EXECUTOR_MODEL_MISMATCH: expected {expected.provider}/{expected.model_id}, "
            f"got {provider}/{model_id}"
        )
    context.actual_executor_provider = provider
    context.actual_executor_model_id = model_id
    return context


def enforce_verifier_model(
    context: ModelExecutionContext,
    *,
    provider: str,
    model_id: str,
) -> ModelExecutionContext:
    """Fail closed if verifier is missing or does not match the bound verifier."""
    if context.route.verifier is None:
        raise ExecutionKernelRepositoryError("VERIFIER_ROUTE_REQUIRED")
    provider = str(provider or "").strip()
    model_id = str(model_id or "").strip()
    expected = context.route.verifier
    if (provider, model_id) != (expected.provider, expected.model_id):
        raise ExecutionKernelRepositoryError(
            f"VERIFIER_MODEL_MISMATCH: expected {expected.provider}/{expected.model_id}, "
            f"got {provider}/{model_id}"
        )
    context.actual_verifier_provider = provider
    context.actual_verifier_model_id = model_id
    return context


def model_routes_payload(contract: ExecutionRouteContract) -> dict[str, Any]:
    """Provider-neutral payload attached to dispatch / start_cycle."""
    out: dict[str, Any] = {
        "executor": {
            **dict(contract.executor.raw),
            "role": "executor",
            "selected_provider": contract.executor.provider,
            "selected_model_id": contract.executor.model_id,
            "route_id": contract.route_id,
            "enforced": True,
        }
    }
    if contract.verifier is not None:
        out["verifier"] = {
            **dict(contract.verifier.raw),
            "role": "verifier",
            "selected_provider": contract.verifier.provider,
            "selected_model_id": contract.verifier.model_id,
            "route_id": contract.route_id,
            "enforced": True,
        }
    return out


@dataclass
class InMemoryRouteAuditLog:
    """Append-only route bind / switch / fallback / enforce audit."""

    _events: list[dict[str, Any]] = field(default_factory=list)

    def append(
        self,
        *,
        tenant_id: str,
        event_type: str,
        work_item_id: str,
        route_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tenant_id = _require_tenant(tenant_id)
        event = {
            "event_id": f"rae_{new_ulid()[:12]}",
            "tenant_id": tenant_id,
            "event_type": event_type,
            "work_item_id": work_item_id,
            "route_id": route_id,
            "payload": dict(payload or {}),
            "recorded_at": _utc_now(),
        }
        self._events.append(event)
        return event

    def list(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        tenant_id = _require_tenant(tenant_id)
        out = [e for e in self._events if e["tenant_id"] == tenant_id]
        if work_item_id:
            out = [e for e in out if e["work_item_id"] == work_item_id]
        return out


@dataclass
class InMemoryExecutionRouteStore:
    """Active route per (tenant, work_item) plus full history for restart survival."""

    _active: dict[tuple[str, str], ExecutionRouteContract] = field(default_factory=dict)
    _history: list[ExecutionRouteContract] = field(default_factory=list)
    audit: InMemoryRouteAuditLog = field(default_factory=InMemoryRouteAuditLog)

    def bind(self, contract: ExecutionRouteContract) -> ExecutionRouteContract:
        key = (contract.tenant_id, contract.work_item_id)
        previous = self._active.get(key)
        if previous is not None and previous.status == "ACTIVE":
            superseded = replace(
                previous, status="SUPERSEDED", superseded_by=contract.route_id
            )
            self._history.append(superseded)
            self.audit.append(
                tenant_id=contract.tenant_id,
                event_type="ROUTE_SUPERSEDED",
                work_item_id=contract.work_item_id,
                route_id=previous.route_id,
                payload={"superseded_by": contract.route_id},
            )
        self._active[key] = contract
        self._history.append(contract)
        self.audit.append(
            tenant_id=contract.tenant_id,
            event_type="ROUTE_BOUND",
            work_item_id=contract.work_item_id,
            route_id=contract.route_id,
            payload={
                "executor": f"{contract.executor.provider}/{contract.executor.model_id}",
                "verifier": (
                    f"{contract.verifier.provider}/{contract.verifier.model_id}"
                    if contract.verifier
                    else None
                ),
                "status": contract.status,
                "switch_reason": contract.switch_reason,
            },
        )
        return contract

    def get_active(
        self, *, tenant_id: str, work_item_id: str
    ) -> ExecutionRouteContract | None:
        tenant_id = _require_tenant(tenant_id)
        return self._active.get((tenant_id, work_item_id))

    def history(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[ExecutionRouteContract]:
        tenant_id = _require_tenant(tenant_id)
        rows = [c for c in self._history if c.tenant_id == tenant_id]
        if work_item_id:
            rows = [c for c in rows if c.work_item_id == work_item_id]
        return rows

    def switch(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        model_routes: dict[str, Any],
        reason: str,
        cycle_id: str | None = None,
        as_fallback: bool = False,
    ) -> ExecutionRouteContract:
        reason = str(reason or "").strip()
        if not reason:
            raise ExecutionKernelRepositoryError("switch reason is required")
        status = "FALLBACK" if as_fallback else "ACTIVE"
        contract = contract_from_model_routes(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            model_routes=model_routes,
            cycle_id=cycle_id,
            status=status,
            switch_reason=reason,
        )
        bound = self.bind(contract)
        self.audit.append(
            tenant_id=tenant_id,
            event_type="ROUTE_FALLBACK" if as_fallback else "ROUTE_SWITCHED",
            work_item_id=work_item_id,
            route_id=bound.route_id,
            payload={"reason": reason},
        )
        return bound

    def context_for(
        self, *, tenant_id: str, work_item_id: str
    ) -> ModelExecutionContext:
        route = self.get_active(tenant_id=tenant_id, work_item_id=work_item_id)
        if route is None:
            raise ExecutionKernelRepositoryError(
                f"no active execution route for work_item={work_item_id}"
            )
        return ModelExecutionContext(route=route)


class RecordingModelExecutor:
    """Test/pilot executor that refuses to run unless enforce_executor_model passes."""

    def __init__(self, *, provider: str, model_id: str) -> None:
        self.provider = provider
        self.model_id = model_id
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self, *, context: ModelExecutionContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        enforce_executor_model(
            context, provider=self.provider, model_id=self.model_id
        )
        record = {
            "provider": self.provider,
            "model_id": self.model_id,
            "route_id": context.route.route_id,
            "payload": dict(payload),
        }
        self.calls.append(record)
        return {"ok": True, "executed_by": record}


__all__ = [
    "RoleRoute",
    "ExecutionRouteContract",
    "ModelExecutionContext",
    "ProviderNeutralExecutor",
    "InMemoryRouteAuditLog",
    "InMemoryExecutionRouteStore",
    "RecordingModelExecutor",
    "role_route_to_dict",
    "role_route_from_gateway",
    "contract_from_model_routes",
    "enforce_executor_model",
    "enforce_verifier_model",
    "model_routes_payload",
]
