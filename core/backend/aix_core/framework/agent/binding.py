"""AIX Framework AgentBinding & Governed Provider Routing Integration.

Sprint: F7 Agent Framework (F7-P2)
Grounded in: GovernedProviderRouter, model_registry.py, agent_roles.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.framework.agent.models import (
    Agent,
    Permission,
    PermissionEffect,
    Persona,
    Role,
    Skill,
)
from backend.infrastructure.model_gateway.provider_routing import (
    GovernedProviderRouter,
    RouteDecisionV1,
)


@dataclass(frozen=True)
class AgentBinding:
    """Canonical binding connecting a logical Agent to its Persona, Skills, and Governed Model Route."""

    agent: Agent
    model_ref: str
    route_decision: RouteDecisionV1

    @classmethod
    def for_role(
        cls,
        role: Role | str,
        *,
        model_ref: str | None = None,
        router: GovernedProviderRouter | None = None,
        persona: Persona | None = None,
        skills: tuple[Skill, ...] = (),
        permissions: tuple[Permission, ...] = (),
    ) -> AgentBinding:
        role_str = role.value if isinstance(role, Role) else str(role)
        role_enum = Role(role_str) if role_str in Role._value2member_map_ else Role.PLANNER

        # Normalize role for model registry lookup
        router_role = "executor" if role_str == "executor_coder" else ("planner" if "planner" in role_str else role_str)

        r = router or GovernedProviderRouter()
        decision = r.resolve_role(router_role, model_ref=model_ref)
        if decision.disposition != "ALLOW":
            raise PermissionError(
                f"GovernedProviderRouter denied route for role {role_str!r}: {decision.reason_codes}"
            )

        resolved_model_ref = decision.route.provider_ref if decision.route else (model_ref or "")

        # Default persona if not supplied
        p = persona or Persona(
            persona_id=f"persona_{role_str}_default",
            name=f"{role_str.capitalize()} Persona",
            role=role_enum,
            system_prompt=f"System prompt for role {role_str}",
            autonomy_cap="L1",
        )

        agent = Agent(
            agent_id=f"agent_{role_str}_bound",
            role=role_enum,
            persona=p,
            skills=skills,
            permissions=permissions,
            autonomy_cap=p.autonomy_cap,
        )

        return cls(
            agent=agent,
            model_ref=resolved_model_ref,
            route_decision=decision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent.to_dict(),
            "model_ref": self.model_ref,
            "route_disposition": self.route_decision.disposition,
            "route": (
                {
                    "provider_ref": self.route_decision.route.provider_ref,
                    "runtime_ref": self.route_decision.route.runtime_ref,
                    "route_ref": self.route_decision.route.route_ref,
                    "gateway": self.route_decision.route.gateway,
                }
                if self.route_decision.route
                else None
            ),
        }
