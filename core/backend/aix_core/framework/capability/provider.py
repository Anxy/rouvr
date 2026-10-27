"""F2-P2 Capability provider resolution.

Resolution flow (fail closed):
  required capability → descriptor lookup → permission/policy check → provider resolution

LLM routes reuse ``GovernedProviderRouter`` — this module does **not** invent a second
model/provider router. Local/Hyeve kinds select registered ``CapabilityProvider``
adapters (wired in F2-P3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from backend.aix_core.framework.capability.models import (
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    CapabilityExecutionResult,
    ProviderKind,
)
from backend.aix_core.framework.capability.registry_facade import (
    CapabilityRegistryFacade,
    get_capability_registry_facade,
)
from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision
from backend.aix_core.runtime.objective.contracts import ActionRequestV1
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
)


class CapabilityProvider(Protocol):
    """Adapter that executes an already-resolved capability request."""

    async def execute(self, request: CapabilityExecutionRequest) -> CapabilityExecutionResult:
        ...


@dataclass(frozen=True)
class CapabilityResolutionResult:
    """Outcome of capability → descriptor → policy → provider resolution."""

    allowed: bool
    capability_id: str
    operation: str
    reason_codes: tuple[str, ...]
    descriptor: CapabilityDescriptor | None = None
    policy: PolicyDecision | None = None
    provider_kind: ProviderKind | None = None
    llm_route: RouteDecisionV1 | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "capability_id": self.capability_id,
            "operation": self.operation,
            "reason_codes": list(self.reason_codes),
            "provider_kind": self.provider_kind.value if self.provider_kind else None,
            "detail": self.detail,
            "policy_code": self.policy.code if self.policy else None,
            "llm_disposition": self.llm_route.disposition if self.llm_route else None,
        }


def _deny(
    *,
    capability_id: str,
    operation: str,
    reason_codes: tuple[str, ...],
    detail: str = "",
    descriptor: CapabilityDescriptor | None = None,
    policy: PolicyDecision | None = None,
    provider_kind: ProviderKind | None = None,
    llm_route: RouteDecisionV1 | None = None,
) -> CapabilityResolutionResult:
    return CapabilityResolutionResult(
        allowed=False,
        capability_id=capability_id,
        operation=operation,
        reason_codes=reason_codes,
        descriptor=descriptor,
        policy=policy,
        provider_kind=provider_kind,
        llm_route=llm_route,
        detail=detail,
    )


class CapabilityProviderResolver:
    """Orchestrates facade + policy + provider selection.

    Does not replace ``GovernedProviderRouter``. LLM inference paths call it directly.
    """

    def __init__(
        self,
        *,
        facade: CapabilityRegistryFacade | None = None,
        governed_router: GovernedProviderRouter | None = None,
    ) -> None:
        self._facade = facade or get_capability_registry_facade()
        # Reuse the single live governance router — never construct a parallel ruleset.
        self._governed = governed_router or GovernedProviderRouter()
        self._providers: dict[ProviderKind, CapabilityProvider] = {}

    @property
    def governed_router(self) -> GovernedProviderRouter:
        return self._governed

    @property
    def facade(self) -> CapabilityRegistryFacade:
        return self._facade

    def register_provider(self, kind: ProviderKind, provider: CapabilityProvider) -> None:
        """Register an execution adapter for a provider kind (F2-P3 wires LOCAL_TOOL)."""
        self._providers[kind] = provider

    def get_provider(self, kind: ProviderKind) -> CapabilityProvider | None:
        return self._providers.get(kind)

    def resolve(
        self,
        request: ActionRequestV1,
        *,
        repository_id: str,
        repository_root: Path | None = None,
        allowed_files: tuple[str, ...] = (),
        apply_edits: bool = True,
        action_budget_remaining: int = 8,
        mutation_budget_remaining: int = 8,
        step_approval_required: bool = False,
        approved: bool = True,
        execution_context: Any = None,
        caller_role: str = "executor",
        model_ref: str | None = None,
        require_registered_provider: bool = False,
        route_kind: str = "governed",
        requested_gateway: str = "hyeve_integration",
    ) -> CapabilityResolutionResult:
        """Fail-closed resolution: capability → descriptor → policy → provider."""
        capability = str(request.capability or "").strip()
        operation = str(request.operation or "").strip()

        # 1) Required capability resolve (foundation catalog via facade)
        resolved = self._facade.resolve(capability, operation)
        if resolved is None:
            return _deny(
                capability_id=capability,
                operation=operation,
                reason_codes=("CAPABILITY_NOT_AVAILABLE",),
                detail=f"capability {capability!r} operation {operation!r} is not registered",
            )
        cap_id, op_name, _spec = resolved

        # 2) Descriptor lookup
        descriptor = self._facade.get_descriptor(cap_id)
        if descriptor is None:
            return _deny(
                capability_id=cap_id,
                operation=op_name,
                reason_codes=("DESCRIPTOR_MISSING",),
                detail=f"no descriptor for capability {cap_id!r}",
            )

        # 3) Permission / policy check (live policy_gateway)
        policy = self._facade.authorize(
            request,
            repository_id=repository_id,
            repository_root=repository_root,
            allowed_files=allowed_files,
            apply_edits=apply_edits,
            action_budget_remaining=action_budget_remaining,
            mutation_budget_remaining=mutation_budget_remaining,
            step_approval_required=step_approval_required,
            approved=approved,
            execution_context=execution_context,
        )
        if not policy.allowed:
            return _deny(
                capability_id=cap_id,
                operation=op_name,
                reason_codes=(policy.code or "POLICY_DENIED",),
                detail=policy.detail or "policy denied",
                descriptor=descriptor,
                policy=policy,
                provider_kind=descriptor.provider_kind,
            )

        kind = descriptor.provider_kind

        # 4) Provider resolution
        if kind == ProviderKind.LLM_INFERENCE:
            ref = (model_ref or "").strip()
            if not ref:
                return _deny(
                    capability_id=cap_id,
                    operation=op_name,
                    reason_codes=("MODEL_REF_REQUIRED",),
                    detail="LLM_INFERENCE requires model_ref",
                    descriptor=descriptor,
                    policy=policy,
                    provider_kind=kind,
                )
            llm_decision = self._governed.resolve(
                role=str(caller_role or "executor"),
                model_ref=ref,
                requested_gateway=requested_gateway,
                route_kind=route_kind,
            )
            if llm_decision.disposition != "ALLOW":
                return _deny(
                    capability_id=cap_id,
                    operation=op_name,
                    reason_codes=llm_decision.reason_codes or ("LLM_ROUTE_DENIED",),
                    detail="GovernedProviderRouter denied LLM route",
                    descriptor=descriptor,
                    policy=policy,
                    provider_kind=kind,
                    llm_route=llm_decision,
                )
            return CapabilityResolutionResult(
                allowed=True,
                capability_id=cap_id,
                operation=op_name,
                reason_codes=("PROVIDER_RESOLVED",) + llm_decision.reason_codes,
                descriptor=descriptor,
                policy=policy,
                provider_kind=kind,
                llm_route=llm_decision,
            )

        # LOCAL_TOOL / HYEVE_SERVICE — kind resolved; optional adapter registration
        if require_registered_provider and kind not in self._providers:
            return _deny(
                capability_id=cap_id,
                operation=op_name,
                reason_codes=("PROVIDER_NOT_REGISTERED",),
                detail=f"no CapabilityProvider registered for {kind.value}",
                descriptor=descriptor,
                policy=policy,
                provider_kind=kind,
            )

        return CapabilityResolutionResult(
            allowed=True,
            capability_id=cap_id,
            operation=op_name,
            reason_codes=("PROVIDER_RESOLVED",),
            descriptor=descriptor,
            policy=policy,
            provider_kind=kind,
        )

    async def route_and_execute(
        self,
        request: CapabilityExecutionRequest,
        *,
        kind: ProviderKind | None = None,
    ) -> CapabilityExecutionResult:
        """Dispatch to a registered adapter only — fail closed if missing.

        Does not call models/providers directly. LLM adapters must use Integration
        via GovernedProviderRouter (enforced at resolve time).
        """
        resolved_kind = kind
        if resolved_kind is None:
            desc = self._facade.get_descriptor(request.capability_id)
            resolved_kind = desc.provider_kind if desc is not None else None
        if resolved_kind is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                success=False,
                error_code="PROVIDER_KIND_UNKNOWN",
                error_message=f"cannot resolve provider kind for {request.capability_id!r}",
            )
        if resolved_kind == ProviderKind.LLM_INFERENCE:
            # Hard gate: no execute without a prior ALLOW route (callers must resolve first).
            ref = (request.model_ref or "").strip()
            decision = self._governed.resolve(
                role=request.caller_role,
                model_ref=ref,
                route_kind="governed",
            )
            if decision.disposition != "ALLOW":
                return CapabilityExecutionResult(
                    request_id=request.request_id,
                    success=False,
                    error_code="LLM_ROUTE_DENIED",
                    error_message=",".join(decision.reason_codes),
                )
        provider = self._providers.get(resolved_kind)
        if provider is None:
            return CapabilityExecutionResult(
                request_id=request.request_id,
                success=False,
                error_code="PROVIDER_NOT_REGISTERED",
                error_message=f"No provider registered for kind: {resolved_kind.value}",
            )
        return await provider.execute(request)


# Back-compat alias matching design pack name — not a second GovernedProviderRouter.
ProviderRouter = CapabilityProviderResolver

__all__ = [
    "CapabilityProvider",
    "CapabilityProviderResolver",
    "CapabilityResolutionResult",
    "ProviderRouter",
]
