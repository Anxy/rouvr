"""Load and validate the five objective-cycle persona manifests."""

from __future__ import annotations

import json
from pathlib import Path

from backend.aix_core.runtime.governance.experimental.agent_roles import (
    FIVE_ROLE_DECISIONS,
    AgentRoleId,
    get_role_decision,
)
from backend.models.persona_manifest_models import (
    PersonaManifestCurrent,
    validate_persona_manifest,
)

OBJECTIVE_CYCLE_PERSONA_PREFIX = "objective_cycle."
_AUTONOMY_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
_REQUIRED_CONSTRAINTS = {
    "no_workhub_lifecycle_mutation",
    "no_auto_accept",
    "no_direct_provider_route",
    "no_recursive_agent_spawn",
}


def load_cycle_persona_manifests(manifests_dir: str | Path) -> dict[AgentRoleId, PersonaManifestCurrent]:
    root = Path(manifests_dir)
    manifests: list[PersonaManifestCurrent] = []
    for path in sorted(root.glob("PM-OBJECTIVE-CYCLE-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifests.append(validate_persona_manifest(payload))
    return validate_cycle_persona_manifests(manifests)


def validate_cycle_persona_manifests(
    manifests: list[PersonaManifestCurrent],
) -> dict[AgentRoleId, PersonaManifestCurrent]:
    by_role: dict[AgentRoleId, PersonaManifestCurrent] = {}
    for manifest in manifests:
        raw_role = str(manifest.metadata.get("objective_cycle_role") or "").strip()
        try:
            role = AgentRoleId(raw_role)
        except ValueError as exc:
            raise ValueError(f"OBJECTIVE_CYCLE_ROLE_INVALID:{raw_role}") from exc
        if role in by_role:
            raise ValueError(f"OBJECTIVE_CYCLE_ROLE_DUPLICATE:{role.value}")
        decision = get_role_decision(role)
        if not manifest.persona_id.startswith(OBJECTIVE_CYCLE_PERSONA_PREFIX):
            raise ValueError(f"OBJECTIVE_CYCLE_PERSONA_ID_INVALID:{manifest.persona_id}")
        if _AUTONOMY_ORDER[manifest.autonomy_cap] > _AUTONOMY_ORDER[decision.autonomy_cap]:
            raise ValueError(f"OBJECTIVE_CYCLE_AUTONOMY_EXCEEDS_ROLE_CAP:{role.value}")
        if not manifest.evidence_policy.require_acceptance_mapping:
            raise ValueError(f"OBJECTIVE_CYCLE_EVIDENCE_MAPPING_REQUIRED:{role.value}")
        missing_constraints = _REQUIRED_CONSTRAINTS - set(manifest.constraints)
        if missing_constraints:
            raise ValueError(f"OBJECTIVE_CYCLE_CONSTRAINTS_MISSING:{role.value}")
        if any("provider" in tool.lower() or "model_call" in tool.lower() for tool in manifest.tool_policy.allowed_tools):
            raise ValueError(f"OBJECTIVE_CYCLE_DIRECT_PROVIDER_TOOL_FORBIDDEN:{role.value}")
        by_role[role] = manifest
    expected = {decision.role_id for decision in FIVE_ROLE_DECISIONS}
    missing = expected - set(by_role)
    if missing:
        raise ValueError(f"OBJECTIVE_CYCLE_PERSONAS_MISSING:{','.join(sorted(role.value for role in missing))}")
    return by_role
