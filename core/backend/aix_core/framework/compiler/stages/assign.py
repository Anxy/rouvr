"""AIX Framework Compiler - Stage 8: Capability & Agent Role Assignment.

Sprint: F10 Work Compiler (F10-P3)
WorkUnit → SkillMatrix → CapabilityRegistryFacade → AgentBinding → GovernedProviderRouter.
No hardcoded domain/task assignments. No direct provider bypass.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.agent.binding import AgentBinding
from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.agent.skill_matrix import SkillMatrix
from backend.aix_core.framework.capability.registry_facade import CapabilityRegistryFacade
from backend.aix_core.framework.work.models import WorkGraph, WorkUnit
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter


class UnitAssignment(BaseModel):
    """Assignment metadata for a single WorkUnit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str
    assigned_role: str
    skills: tuple[dict[str, Any], ...]
    required_capabilities: tuple[str, ...]
    resolved_capabilities: tuple[str, ...] = ()
    model_ref: str | None = None
    provider_ref: str | None = None
    route_disposition: str | None = None
    is_governed: bool


class AssignedWorkGraph(BaseModel):
    """WorkGraph decorated with verified agent role and capability assignments."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph: WorkGraph
    assignments: dict[str, UnitAssignment] = Field(default_factory=dict)


def _role_from_raw(raw_role: str, default_role: Role) -> Role:
    text = str(raw_role or "").strip().lower()
    if text in ("executor", "executor_coder", "coder"):
        return Role.EXECUTOR_CODER
    if text == "planner":
        return Role.PLANNER
    if text in ("verifier", "tester"):
        return Role.VERIFIER
    if text in ("recovery", "recovery_planner"):
        return Role.RECOVERY_PLANNER
    if text in ("reviewer", "review_packet_curator"):
        return Role.REVIEW_PACKET_CURATOR
    return default_role


# SkillMatrix may declare logical names; map onto foundation CapabilityRegistry ids.
_CAPABILITY_ALIASES: dict[str, str] = {
    "workspace": "filesystem",
    "evidence": "content",
    "shell": "process",
}


def _capabilities_for_role(role: Role, facade: CapabilityRegistryFacade) -> tuple[str, ...]:
    """Resolve capability IDs from SkillMatrix required_capabilities ∩ live catalog."""
    registered = frozenset(d.capability_id for d in facade.list_capabilities())
    caps: list[str] = []
    for skill in SkillMatrix.skills_for_role(role):
        for cap in getattr(skill, "required_capabilities", ()) or ():
            cid = str(cap or "").strip()
            mapped = _CAPABILITY_ALIASES.get(cid, cid)
            if mapped in registered and mapped not in caps:
                caps.append(mapped)
            elif cid in registered and cid not in caps:
                caps.append(cid)
    if not caps and "filesystem" in registered:
        caps.append("filesystem")
    return tuple(caps)


class AssignStage:
    """Stage 8: Capability + agent assignment via F2 facade + F7 SkillMatrix + GPR."""

    @classmethod
    def run(
        cls,
        graph: WorkGraph,
        *,
        default_role: Role = Role.EXECUTOR_CODER,
        facade: CapabilityRegistryFacade | None = None,
        router: GovernedProviderRouter | None = None,
        bind_models: bool = True,
    ) -> AssignedWorkGraph:
        cap_facade = facade or CapabilityRegistryFacade()
        gpr = router or GovernedProviderRouter()
        assignments: dict[str, UnitAssignment] = {}
        enriched_units: list[WorkUnit] = []

        for unit in graph.units:
            role_enum = _role_from_raw(
                unit.required_agent_role or unit.contract.assigned_role or default_role.value,
                default_role,
            )
            skills = tuple(s.to_dict() for s in SkillMatrix.skills_for_role(role_enum))
            required_caps = _capabilities_for_role(role_enum, cap_facade)
            resolved: list[str] = []
            for cap_id in required_caps:
                # Presence in catalog = resolved; operations validated at execute time.
                if cap_facade.get_descriptor(cap_id) is not None:
                    resolved.append(cap_id)

            model_ref = None
            provider_ref = None
            route_disposition = None
            if bind_models:
                try:
                    binding = AgentBinding.for_role(role_enum, router=gpr)
                    model_ref = binding.model_ref
                    if binding.route_decision.route:
                        provider_ref = binding.route_decision.route.provider_ref
                    route_disposition = binding.route_decision.disposition
                except PermissionError:
                    route_disposition = "DENY"
                    # Assignment still records denial — execute path fail-closes via GPR.
                except Exception:
                    route_disposition = "ERROR"

            assignment = UnitAssignment(
                unit_id=unit.unit_id,
                assigned_role=role_enum.value,
                skills=skills,
                required_capabilities=required_caps,
                resolved_capabilities=tuple(resolved),
                model_ref=model_ref,
                provider_ref=provider_ref,
                route_disposition=route_disposition,
                is_governed=True,
            )
            assignments[unit.unit_id] = assignment
            enriched_units.append(
                unit.model_copy(
                    update={
                        "required_capabilities": required_caps,
                        "required_agent_role": role_enum.value,
                    }
                )
            )

        new_graph = graph.model_copy(update={"units": tuple(enriched_units)})
        if not new_graph.execution_order:
            new_graph = new_graph.model_copy(
                update={"execution_order": new_graph.compute_execution_order()}
            )
        return AssignedWorkGraph(graph=new_graph, assignments=assignments)
