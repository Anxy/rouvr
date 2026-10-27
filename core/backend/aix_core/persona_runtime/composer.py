"""compose(): pure function, Persona+Capabilities+Policy+Overlay+objective+budget -> RuntimeContextV1.

Same inputs always produce the same RuntimeContextV1 -- no I/O, no service
dependencies, no randomness, no clock reads. The non-escalation guarantee
(an overlay can never widen what its base Policy grants) is enforced here,
structurally, as part of computing the resolved grant -- not as a separate
validator that runs after the fact.
"""
from __future__ import annotations

from typing import Any

from backend.aix_core.persona_runtime.entities import (
    AUTONOMY_ORDER,
    CapabilityV1,
    OverlayV1,
    PersonaV1,
    PolicyV1,
)
from backend.aix_core.persona_runtime.runtime_context import RuntimeContextV1


class ComposerError(ValueError):
    """Raised when inputs cannot be composed -- includes attempted escalation."""


def compose(
    *,
    persona: PersonaV1,
    capabilities: tuple[CapabilityV1, ...],
    policy: PolicyV1,
    overlay: OverlayV1 | None,
    objective: str,
    budget: dict[str, Any] | None = None,
) -> RuntimeContextV1:
    if not objective.strip():
        raise ComposerError("PERSONA_RUNTIME_OBJECTIVE_REQUIRED")

    if overlay is not None and overlay.tenant_id.strip() == "":
        raise ComposerError("PERSONA_RUNTIME_OVERLAY_TENANT_REQUIRED")

    # --- resolve which capabilities the persona actually requested ---
    by_id = {capability.capability_id: capability for capability in capabilities}
    resolved_capabilities: list[CapabilityV1] = []
    missing_capability_ids: list[str] = []
    for capability_id in persona.requested_capability_ids:
        capability = by_id.get(capability_id)
        if capability is None:
            missing_capability_ids.append(capability_id)
        else:
            resolved_capabilities.append(capability)

    knowledge = tuple(
        dict.fromkeys(line for capability in resolved_capabilities for line in capability.knowledge)
    )
    verification_rules = tuple(
        dict.fromkeys(rule for capability in resolved_capabilities for rule in capability.verification_rules)
    )
    tool_invocation_protocol = tuple(
        dict.fromkeys(
            line for capability in resolved_capabilities for line in capability.tool_invocation_protocol
        )
    )
    requested_tools = tuple(
        dict.fromkeys(tool for capability in resolved_capabilities for tool in capability.requested_tools)
    )

    # --- non-escalation enforcement: this is the guarantee, not a bolt-on check ---
    effective_autonomy_cap = policy.autonomy_cap
    effective_granted_tools = set(policy.granted_tools)
    evidence_requirements = set(policy.evidence_requirements)
    overlay_id: str | None = None

    if overlay is not None:
        overlay_id = overlay.overlay_id

        if overlay.autonomy_cap_override is not None:
            if AUTONOMY_ORDER[overlay.autonomy_cap_override] > AUTONOMY_ORDER[policy.autonomy_cap]:
                raise ComposerError(
                    "PERSONA_RUNTIME_OVERLAY_AUTONOMY_ESCALATION:"
                    f"base={policy.autonomy_cap},override={overlay.autonomy_cap_override}"
                )
            effective_autonomy_cap = overlay.autonomy_cap_override

        if overlay.granted_tools_override is not None:
            overlay_tools = set(overlay.granted_tools_override)
            widened = overlay_tools - effective_granted_tools
            if widened:
                raise ComposerError(
                    "PERSONA_RUNTIME_OVERLAY_TOOL_ESCALATION:" f"widened_tools={sorted(widened)}"
                )
            effective_granted_tools = overlay_tools

        # Evidence requirements are purely additive for an overlay -- this can only make
        # the resolved context stricter, never looser, so it needs no escalation check.
        evidence_requirements |= set(overlay.evidence_requirements_add)

    # --- the core operation: granted is always requested INTERSECT granted, never granted alone ---
    requested_tool_set = set(requested_tools)
    granted_tools = tuple(dict.fromkeys(tool for tool in requested_tools if tool in effective_granted_tools))
    denied_requested_tools = tuple(
        dict.fromkeys(tool for tool in requested_tools if tool not in effective_granted_tools)
    )
    assert requested_tool_set == set(granted_tools) | set(denied_requested_tools)

    return RuntimeContextV1(
        objective=objective.strip(),
        budget=dict(budget or {}),
        persona_id=persona.persona_id,
        mission=persona.mission,
        communication_style=persona.communication_style,
        reasoning_style=persona.reasoning_style,
        capability_ids=tuple(capability.capability_id for capability in resolved_capabilities),
        knowledge=knowledge,
        verification_rules=verification_rules,
        tool_invocation_protocol=tool_invocation_protocol,
        missing_capability_ids=tuple(missing_capability_ids),
        policy_id=policy.policy_id,
        overlay_id=overlay_id,
        autonomy_cap=effective_autonomy_cap,
        constraints=policy.constraints,
        requested_tools=requested_tools,
        granted_tools=granted_tools,
        denied_requested_tools=denied_requested_tools,
        evidence_requirements=tuple(sorted(evidence_requirements)),
    )
