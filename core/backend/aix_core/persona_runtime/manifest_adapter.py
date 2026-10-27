"""Adapter: real PersonaManifestCurrent (+ real AgentRoleDecision) -> real PersonaV1/PolicyV1/CapabilityV1.

Every field populated here is sourced from an actual file in the repo -- the
five PM-OBJECTIVE-CYCLE-*-V1.json manifests, agent_roles.py's
FIVE_ROLE_DECISIONS, and (for executor_coder only) the real, running
PlannerPromptBuilder in aix_prompt_builder_service.py. Where no real source
content exists for a field, this module leaves it empty rather than
inventing plausible-looking text -- see MISSING_REAL_CONTENT below for the
explicit, honest accounting of what that means per role.
"""
from __future__ import annotations

from pathlib import Path

from backend.aix_core.runtime.governance.experimental.agent_roles import AgentRoleId, get_role_decision
from backend.aix_core.runtime.governance.experimental.persona_manifests import load_cycle_persona_manifests
from backend.aix_core.persona_runtime.entities import CapabilityV1, PersonaV1, PolicyV1
from backend.models.persona_manifest_models import PersonaManifestCurrent
from backend.services.aix_prompt_builder_service import PlannerPromptBuilder

MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "config" / "persona_manifests"

# Only executor_coder has any real, production-sourced prompt content anywhere in the
# repo today (PlannerPromptBuilder's generic lane, reached via responsibility-based
# analogy -- devexecutor's "code mutation planner" role is not code-wired to
# AgentRoleId.EXECUTOR_CODER, but is the closest real content that exists for a role
# whose `may` includes "produce_diff_or_artifact"). For the remaining four roles,
# grep across aix_core/objective_cycle/{verification,replan,evidence_bundle}.py and
# every other module in that package found zero prompt/LLM-call references tied to
# AgentRoleId.PLANNER/VERIFIER/RECOVERY_PLANNER/REVIEW_PACKET_CURATOR. There is no
# real prose to source for their `knowledge`, `verification_rules`, or
# `tool_invocation_protocol` -- this dict records that as fact, not as a TODO.
MISSING_REAL_CONTENT: dict[AgentRoleId, str] = {
    AgentRoleId.PLANNER: (
        "No production prompt-building code path exists for the objective-cycle 'planner' role. "
        "PlannerPromptBuilder's role='code mutation planner' is a different, unwired role name."
    ),
    AgentRoleId.VERIFIER: (
        "No production prompt-building code path exists for 'verifier'. Its real `may` list "
        "(run_read_only_validation, issue_step_verdict) has no LLM-facing implementation found."
    ),
    AgentRoleId.RECOVERY_PLANNER: (
        "No production prompt-building code path exists for 'recovery_planner'. "
        "aix_core/objective_cycle/replan.py has zero prompt/LLM references."
    ),
    AgentRoleId.REVIEW_PACKET_CURATOR: (
        "No production prompt-building code path exists for 'review_packet_curator'. "
        "aix_core/objective_cycle/evidence_bundle.py has zero prompt/LLM references."
    ),
}


def load_real_manifests() -> dict[AgentRoleId, PersonaManifestCurrent]:
    """Loads and validates the five real PM-OBJECTIVE-CYCLE-*-V1.json files via the
    real, existing validator (persona_manifests.load_cycle_persona_manifests) -- not
    a reimplementation."""
    return load_cycle_persona_manifests(MANIFESTS_DIR)


def persona_from_manifest(manifest: PersonaManifestCurrent, role: AgentRoleId) -> PersonaV1:
    decision = get_role_decision(role)
    return PersonaV1(
        persona_id=manifest.persona_id,
        persona_name=manifest.persona_name,
        persona_version=manifest.persona_version,
        # Sourced from the real AgentRoleDecision.responsibility -- not invented prose.
        mission=decision.responsibility,
        # No real communication_style/reasoning_style content exists anywhere in the repo
        # for any of the five roles -- left empty rather than filled with plausible text.
        communication_style=(),
        reasoning_style=(),
        requested_capability_ids=(f"cap.{role.value}.tools",),
    )


def policy_from_manifest(manifest: PersonaManifestCurrent) -> PolicyV1:
    return PolicyV1(
        policy_id=manifest.persona_id,
        policy_version=manifest.persona_version,
        autonomy_cap=manifest.autonomy_cap,
        granted_tools=tuple(manifest.tool_policy.allowed_tools),
        evidence_requirements=(
            manifest.evidence_policy.evidence_pack_schema,
            *(("acceptance_mapping",) if manifest.evidence_policy.require_acceptance_mapping else ()),
        ),
        constraints=tuple(manifest.constraints),
    )


def _extract_section(rendered: str, title: str) -> tuple[str, ...]:
    lines = rendered.splitlines()
    try:
        start = lines.index(title) + 1
    except ValueError:
        return ()
    out: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            break
        out.append(line)
    return tuple(out)


def _real_executor_coder_tool_invocation_protocol() -> tuple[str, ...]:
    """Extracted verbatim, at runtime, from the real PlannerPromptBuilder.system_prompt's
    generic-lane output -- not hand-copied into a second static string that could drift
    out of sync with the source."""
    rendered = PlannerPromptBuilder.system_prompt(packet={}, lane="generic")
    return _extract_section(rendered, "Layer B: Lane Instructions")


def capabilities_from_manifest(manifest: PersonaManifestCurrent, role: AgentRoleId) -> tuple[CapabilityV1, ...]:
    tool_invocation_protocol: tuple[str, ...] = ()
    if role is AgentRoleId.EXECUTOR_CODER:
        tool_invocation_protocol = _real_executor_coder_tool_invocation_protocol()

    return (
        CapabilityV1(
            capability_id=f"cap.{role.value}.tools",
            capability_name=f"{role.value} tool access",
            knowledge=(),
            verification_rules=(),
            tool_invocation_protocol=tool_invocation_protocol,
            requested_tools=tuple(manifest.tool_policy.allowed_tools),
        ),
    )
