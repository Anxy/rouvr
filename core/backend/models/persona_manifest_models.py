from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PERSONA_MANIFEST_SCHEMA_VERSION_Current = "persona_manifest.current"
TENANT_OVERLAY_SCHEMA_VERSION_Current = "tenant_overlay.current"
SUPPORTED_PERSONA_MANIFEST_VERSIONS = {PERSONA_MANIFEST_SCHEMA_VERSION_Current}
SUPPORTED_TENANT_OVERLAY_VERSIONS = {TENANT_OVERLAY_SCHEMA_VERSION_Current}
AUTONOMY_LEVEL = Literal["L0", "L1", "L2", "L3", "L4"]


class PersonaToolPolicyRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_commands_profile: str = Field(
        ...,
        min_length=1,
        description="Reference to command allowlist profile (for example ACP-CODER-L2-CURRENT).",
    )
    repo_profile: str = Field(
        ...,
        min_length=1,
        description="Reference to repo execution profile (for example RP-HYEVE-PY-FASTAPI-CURRENT).",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Optional explicit tool capability list for this persona.",
    )


class PersonaEvidencePolicyRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_pack_schema: str = Field(
        ...,
        min_length=1,
        description="Versioned evidence pack schema id expected from executor runs.",
    )
    require_acceptance_mapping: bool = Field(
        default=True,
        description="Require acceptance-to-evidence mapping table in the evidence pack.",
    )


class PersonaEnforcerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_checklist: bool = Field(
        default=True,
        description="Require FUTURE-TASK-PROMPT-CHECKLIST compliance before execution.",
    )
    requires_hostile_sweep: bool = Field(
        default=False,
        description="Require hostile/negative sweep evidence before closeout.",
    )
    requires_canonical_discipline: bool = Field(
        default=True,
        description="Require canonical source and no-fallback discipline for the persona.",
    )
    negative_proofs: list[str] = Field(
        default_factory=list,
        description="Negative proof classes the persona must cover before closeout.",
    )


class PersonaMetaCoderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activate_from_forensics: bool = Field(
        default=False,
        description="Allow self-improvement tasks to be activated from forensic findings.",
    )
    improve_legacy: bool = Field(
        default=False,
        description="Allow bounded modernization of legacy implementation patterns.",
    )
    self_improvement_from_sweeps: list[str] = Field(
        default_factory=list,
        description="Sweep classes that may seed bounded self-improvement work.",
    )


class PersonaManifestCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["persona_manifest.current"] = Field(
        ...,
        description="Versioned persona manifest schema id.",
    )
    persona_id: str = Field(..., min_length=1)
    persona_name: str = Field(..., min_length=1)
    persona_version: str = Field(default="v1", min_length=1)
    autonomy_cap: AUTONOMY_LEVEL = Field(
        ...,
        description="Maximum autonomy ladder level permitted for this persona.",
    )
    policy_engine_profile: str = Field(
        ...,
        min_length=1,
        description="Reference to AIX Policy Engine profile id.",
    )
    tool_policy: PersonaToolPolicyRef
    evidence_policy: PersonaEvidencePolicyRef
    constraints: list[str] = Field(default_factory=list)
    enforcer_policy: PersonaEnforcerPolicy | None = Field(
        default=None,
        description="Optional strict execution-enforcer policy for coder personas.",
    )
    meta_coder_policy: PersonaMetaCoderPolicy | None = Field(
        default=None,
        description="Optional bounded self-improvement policy for coder personas.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantOverlayToolPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_commands_profile: str | None = Field(default=None, min_length=1)
    repo_profile: str | None = Field(default=None, min_length=1)


class TenantOverlayEvidencePolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_pack_schema: str | None = Field(default=None, min_length=1)
    require_acceptance_mapping: bool | None = None


class TenantOverlayOverridesCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autonomy_cap_override: AUTONOMY_LEVEL | None = None
    policy_engine_profile: str | None = Field(default=None, min_length=1)
    tool_policy: TenantOverlayToolPolicyOverride | None = None
    evidence_policy: TenantOverlayEvidencePolicyOverride | None = None
    constraints_add: list[str] = Field(default_factory=list)
    constraints_remove: list[str] = Field(default_factory=list)


class TenantOverlayCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["tenant_overlay.current"] = Field(
        ...,
        description="Versioned tenant overlay schema id.",
    )
    tenant_id: str = Field(..., min_length=1)
    persona_id: str = Field(..., min_length=1)
    overrides: TenantOverlayOverridesCurrent
    metadata: dict[str, Any] = Field(default_factory=dict)


def get_persona_manifest_schema_current() -> dict[str, Any]:
    return PersonaManifestCurrent.model_json_schema()


def get_tenant_overlay_schema_current() -> dict[str, Any]:
    return TenantOverlayCurrent.model_json_schema()


def validate_persona_manifest(payload: dict[str, Any]) -> PersonaManifestCurrent:
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in SUPPORTED_PERSONA_MANIFEST_VERSIONS:
        raise ValueError(
            f"Unsupported persona manifest schema_version '{schema_version}'. "
            f"Supported: {sorted(SUPPORTED_PERSONA_MANIFEST_VERSIONS)}"
        )
    return PersonaManifestCurrent.model_validate(payload)


def validate_tenant_overlay(payload: dict[str, Any]) -> TenantOverlayCurrent:
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in SUPPORTED_TENANT_OVERLAY_VERSIONS:
        raise ValueError(
            f"Unsupported tenant overlay schema_version '{schema_version}'. "
            f"Supported: {sorted(SUPPORTED_TENANT_OVERLAY_VERSIONS)}"
        )
    return TenantOverlayCurrent.model_validate(payload)


def validate_tenant_overlay_policy(overlay: TenantOverlayCurrent, base_manifest: PersonaManifestCurrent) -> None:
    override = overlay.overrides.autonomy_cap_override
    if override is None:
        pass
    else:
        order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
        if order[override] > order[base_manifest.autonomy_cap]:
            raise ValueError(
                "Tenant overlay autonomy escalation is not allowed: "
                f"base={base_manifest.autonomy_cap}, override={override}"
            )

    # Enforce non-escalating overlay semantics for coder/verifier runtime.
    if (
        overlay.overrides.policy_engine_profile
        and overlay.overrides.policy_engine_profile != base_manifest.policy_engine_profile
    ):
        raise ValueError(
            "Tenant overlay policy_engine_profile override is not allowed for non-escalating overlays: "
            f"base={base_manifest.policy_engine_profile}, override={overlay.overrides.policy_engine_profile}"
        )

    tool_override = overlay.overrides.tool_policy
    if tool_override is not None:
        if (
            tool_override.allowed_commands_profile
            and tool_override.allowed_commands_profile != base_manifest.tool_policy.allowed_commands_profile
        ):
            raise ValueError(
                "Tenant overlay allowed_commands_profile override is not allowed for non-escalating overlays: "
                f"base={base_manifest.tool_policy.allowed_commands_profile}, "
                f"override={tool_override.allowed_commands_profile}"
            )
        if tool_override.repo_profile and tool_override.repo_profile != base_manifest.tool_policy.repo_profile:
            raise ValueError(
                "Tenant overlay repo_profile override is not allowed for non-escalating overlays: "
                f"base={base_manifest.tool_policy.repo_profile}, override={tool_override.repo_profile}"
            )

    evidence_override = overlay.overrides.evidence_policy
    if evidence_override is not None:
        if (
            evidence_override.evidence_pack_schema
            and evidence_override.evidence_pack_schema != base_manifest.evidence_policy.evidence_pack_schema
        ):
            raise ValueError(
                "Tenant overlay evidence_pack_schema override is not allowed for non-escalating overlays: "
                f"base={base_manifest.evidence_policy.evidence_pack_schema}, "
                f"override={evidence_override.evidence_pack_schema}"
            )
        if (
            evidence_override.require_acceptance_mapping is False
            and base_manifest.evidence_policy.require_acceptance_mapping
        ):
            raise ValueError("Tenant overlay cannot disable require_acceptance_mapping when base manifest requires it.")


READ_ONLY_TOOL_SET = {"list_dir", "read_file", "search", "get_status", "evidence.attach"}


def validate_read_only_tool_surface(manifest: PersonaManifestCurrent) -> None:
    tools = list(manifest.tool_policy.allowed_tools)
    if not tools:
        return
    denied = sorted({tool for tool in tools if tool not in READ_ONLY_TOOL_SET})
    if denied:
        raise ValueError(f"READ_ONLY_TOOL_POLICY_VIOLATION disallowed_tools={denied}")
