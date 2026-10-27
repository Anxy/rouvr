"""001D-W04: Governed provider routing — Hyeve Integration only.

No direct provider clients. Routes must resolve through Integration identity
layering (provider_ref + runtime_ref + route_ref). Fail closed on bypass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RouteDisposition = Literal["ALLOW", "DENY"]

ALLOWED_GATEWAYS = frozenset({"hyeve_integration", "aix_model_gateway"})
FORBIDDEN_ROUTE_KINDS = frozenset({"direct_provider", "direct_openai", "direct_anthropic", "raw_http_provider"})


@dataclass(frozen=True)
class RouteIdentityV1:
    provider_ref: str
    runtime_ref: str
    route_ref: str
    gateway: str = "hyeve_integration"
    route_kind: str = "governed"


@dataclass(frozen=True)
class RouteDecisionV1:
    disposition: RouteDisposition
    route: RouteIdentityV1 | None
    reason_codes: tuple[str, ...]
    evidence: dict[str, str]


class GovernedProviderRouter:
    """Resolve an approved Integration-backed route for a role/model request."""

    def resolve_role(self, role: str, *, model_ref: str | None = None) -> RouteDecisionV1:
        """Resolve using the intelligence-layer registry when model_ref is omitted."""
        resolved = (model_ref or "").strip()
        if not resolved:
            from backend.infrastructure.model_gateway.intelligence_layer.model_registry import (
                load_model_registry,
            )

            resolved = load_model_registry().model_ref_for(role)  # type: ignore[arg-type]
        return self.resolve(role=role, model_ref=resolved)

    def resolve(
        self,
        *,
        role: str,
        model_ref: str,
        requested_gateway: str = "hyeve_integration",
        route_kind: str = "governed",
        provider_ref: str | None = None,
        runtime_ref: str | None = None,
        route_ref: str | None = None,
    ) -> RouteDecisionV1:
        reasons: list[str] = []
        if route_kind in FORBIDDEN_ROUTE_KINDS or route_kind.startswith("direct_"):
            return RouteDecisionV1(
                disposition="DENY",
                route=None,
                reason_codes=("DIRECT_PROVIDER_ROUTE_FORBIDDEN",),
                evidence={"route_kind": route_kind, "model_ref": model_ref, "role": role},
            )
        if requested_gateway not in ALLOWED_GATEWAYS:
            return RouteDecisionV1(
                disposition="DENY",
                route=None,
                reason_codes=("GATEWAY_NOT_ALLOWED",),
                evidence={"gateway": requested_gateway},
            )
        if not model_ref or not model_ref.strip():
            return RouteDecisionV1(
                disposition="DENY",
                route=None,
                reason_codes=("MODEL_REF_REQUIRED",),
                evidence={"role": role},
            )
        provider = (provider_ref or f"integration:{model_ref}").strip()
        runtime = (runtime_ref or "hyeve-integration-runtime").strip()
        route = (route_ref or f"route:{role}:{model_ref}").strip()
        if provider.startswith("direct://") or provider.startswith("https://api.openai") or "bypass" in provider:
            reasons.append("PROVIDER_REF_LOOKS_DIRECT")
        if reasons:
            return RouteDecisionV1(
                disposition="DENY",
                route=None,
                reason_codes=tuple(reasons),
                evidence={"provider_ref": provider},
            )
        identity = RouteIdentityV1(
            provider_ref=provider,
            runtime_ref=runtime,
            route_ref=route,
            gateway=requested_gateway,
            route_kind=route_kind,
        )
        return RouteDecisionV1(
            disposition="ALLOW",
            route=identity,
            reason_codes=("GOVERNED_ROUTE_RESOLVED",),
            evidence={
                "role": role,
                "model_ref": model_ref,
                "gateway": identity.gateway,
                "route_ref": identity.route_ref,
            },
        )
