from __future__ import annotations

from typing import Any

from backend.models.persona_manifest_models import (
    PersonaManifestCurrent,
    TenantOverlayCurrent,
    validate_tenant_overlay_policy,
)


def _unique_ordered(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def resolve_persona_manifest(
    *,
    base_manifest: PersonaManifestCurrent,
    tenant_overlay: TenantOverlayCurrent | None = None,
    runtime_safe_defaults: dict[str, Any] | None = None,
) -> tuple[PersonaManifestCurrent, dict[str, Any]]:
    working = base_manifest.model_dump(mode="python")
    trace: list[dict[str, Any]] = []

    if tenant_overlay is not None:
        if tenant_overlay.persona_id != base_manifest.persona_id:
            raise ValueError(
                "Tenant overlay persona mismatch: "
                f"base={base_manifest.persona_id}, overlay={tenant_overlay.persona_id}"
            )
        validate_tenant_overlay_policy(tenant_overlay, base_manifest)
        o = tenant_overlay.overrides
        if o.autonomy_cap_override is not None:
            working["autonomy_cap"] = o.autonomy_cap_override
            trace.append({"source": "tenant_overlay", "field": "autonomy_cap"})
        if o.policy_engine_profile:
            working["policy_engine_profile"] = o.policy_engine_profile
            trace.append({"source": "tenant_overlay", "field": "policy_engine_profile"})
        if o.tool_policy is not None:
            if o.tool_policy.allowed_commands_profile:
                working["tool_policy"]["allowed_commands_profile"] = o.tool_policy.allowed_commands_profile
                trace.append({"source": "tenant_overlay", "field": "tool_policy.allowed_commands_profile"})
            if o.tool_policy.repo_profile:
                working["tool_policy"]["repo_profile"] = o.tool_policy.repo_profile
                trace.append({"source": "tenant_overlay", "field": "tool_policy.repo_profile"})
        if o.evidence_policy is not None:
            if o.evidence_policy.evidence_pack_schema:
                working["evidence_policy"]["evidence_pack_schema"] = o.evidence_policy.evidence_pack_schema
                trace.append({"source": "tenant_overlay", "field": "evidence_policy.evidence_pack_schema"})
            if o.evidence_policy.require_acceptance_mapping is not None:
                working["evidence_policy"]["require_acceptance_mapping"] = o.evidence_policy.require_acceptance_mapping
                trace.append({"source": "tenant_overlay", "field": "evidence_policy.require_acceptance_mapping"})
        if o.constraints_add:
            working["constraints"] = _unique_ordered([*working.get("constraints", []), *o.constraints_add])
            trace.append({"source": "tenant_overlay", "field": "constraints_add"})
        if o.constraints_remove:
            remove = set(o.constraints_remove)
            working["constraints"] = [x for x in working.get("constraints", []) if x not in remove]
            trace.append({"source": "tenant_overlay", "field": "constraints_remove"})

    defaults = runtime_safe_defaults or {}
    if defaults.get("require_acceptance_mapping") is True:
        working["evidence_policy"]["require_acceptance_mapping"] = True
        trace.append({"source": "runtime_safe_defaults", "field": "evidence_policy.require_acceptance_mapping"})
    if defaults.get("append_constraints"):
        to_add = [str(x) for x in defaults.get("append_constraints", []) if str(x).strip()]
        working["constraints"] = _unique_ordered([*working.get("constraints", []), *to_add])
        trace.append({"source": "runtime_safe_defaults", "field": "constraints"})

    resolved = PersonaManifestCurrent.model_validate(working)
    diagnostics = {
        "base_persona_id": base_manifest.persona_id,
        "overlay_applied": tenant_overlay is not None,
        "changes": trace,
    }
    return resolved, diagnostics
