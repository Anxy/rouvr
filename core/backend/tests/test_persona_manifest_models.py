from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.persona_manifest_models import (
    PERSONA_MANIFEST_SCHEMA_VERSION_Current,
    TENANT_OVERLAY_SCHEMA_VERSION_Current,
    get_persona_manifest_schema_current,
    get_tenant_overlay_schema_current,
    validate_persona_manifest,
    validate_read_only_tool_surface,
    validate_tenant_overlay,
    validate_tenant_overlay_policy,
)
from backend.services.persona_overlay_resolver_service import resolve_persona_manifest


def _minimal_payload() -> dict:
    return {
        "schema_version": PERSONA_MANIFEST_SCHEMA_VERSION_Current,
        "persona_id": "coder.current",
        "persona_name": "Coder Persona v1",
        "autonomy_cap": "L2",
        "policy_engine_profile": "PE-CODER-L2-CURRENT",
        "tool_policy": {
            "allowed_commands_profile": "ACP-CODER-L2-CURRENT",
            "repo_profile": "RP-HYEVE-PY-FASTAPI-CURRENT",
        },
        "evidence_policy": {
            "evidence_pack_schema": "evidence_pack.current",
            "require_acceptance_mapping": True,
        },
    }


def test_validate_persona_manifest_minimal() -> None:
    payload = _minimal_payload()
    manifest = validate_persona_manifest(payload)
    assert manifest.schema_version == PERSONA_MANIFEST_SCHEMA_VERSION_Current
    assert manifest.autonomy_cap == "L2"
    assert manifest.tool_policy.repo_profile == "RP-HYEVE-PY-FASTAPI-CURRENT"


def test_validate_persona_manifest_full() -> None:
    payload = _minimal_payload()
    payload["constraints"] = ["no_auto_done", "patch_first"]
    payload["metadata"] = {"source": "tenant_overlay_resolver"}
    manifest = validate_persona_manifest(payload)
    assert manifest.constraints == ["no_auto_done", "patch_first"]
    assert manifest.metadata["source"] == "tenant_overlay_resolver"


def test_validate_persona_manifest_rejects_unsupported_version() -> None:
    payload = _minimal_payload()
    payload["schema_version"] = "persona_manifest.v2"
    with pytest.raises(ValueError, match="Unsupported persona manifest schema_version"):
        validate_persona_manifest(payload)


def test_validate_persona_manifest_rejects_invalid_autonomy_cap() -> None:
    payload = _minimal_payload()
    payload["autonomy_cap"] = "L9"
    with pytest.raises(Exception):
        validate_persona_manifest(payload)


def test_persona_manifest_schema_contains_required_contract_fields() -> None:
    schema = get_persona_manifest_schema_current()
    required = set(schema.get("required") or [])
    assert {"schema_version", "persona_id", "autonomy_cap", "tool_policy", "evidence_policy"} <= required


def _overlay_payload() -> dict:
    return {
        "schema_version": TENANT_OVERLAY_SCHEMA_VERSION_Current,
        "tenant_id": "default",
        "persona_id": "coder.current",
        "overrides": {
            "autonomy_cap_override": "L2",
            "policy_engine_profile": "PE-CODER-L2-CURRENT",
            "tool_policy": {"allowed_commands_profile": "ACP-CODER-L2-CURRENT"},
            "constraints_add": ["enforce_breakglass_ticket"],
            "constraints_remove": ["legacy_constraint"],
        },
    }


def test_validate_tenant_overlay_allow_matrix() -> None:
    overlay = validate_tenant_overlay(_overlay_payload())
    assert overlay.overrides.tool_policy is not None
    assert overlay.overrides.tool_policy.allowed_commands_profile == "ACP-CODER-L2-CURRENT"


def test_validate_tenant_overlay_rejects_unknown_override_field() -> None:
    payload = _overlay_payload()
    payload["overrides"]["unknown_field"] = "not-allowed"
    with pytest.raises(Exception):
        validate_tenant_overlay(payload)


def test_validate_tenant_overlay_policy_blocks_autonomy_escalation() -> None:
    manifest = validate_persona_manifest(_minimal_payload())
    payload = _overlay_payload()
    payload["overrides"]["autonomy_cap_override"] = "L3"
    overlay = validate_tenant_overlay(payload)
    with pytest.raises(ValueError, match="autonomy escalation is not allowed"):
        validate_tenant_overlay_policy(overlay, manifest)


def test_validate_tenant_overlay_policy_blocks_command_profile_override() -> None:
    manifest = validate_persona_manifest(_minimal_payload())
    payload = _overlay_payload()
    payload["overrides"]["tool_policy"]["allowed_commands_profile"] = "ACP-CODER-L3-CURRENT"
    overlay = validate_tenant_overlay(payload)
    with pytest.raises(ValueError, match="allowed_commands_profile override is not allowed"):
        validate_tenant_overlay_policy(overlay, manifest)


def test_validate_tenant_overlay_policy_blocks_repo_profile_override() -> None:
    manifest = validate_persona_manifest(_minimal_payload())
    payload = _overlay_payload()
    payload["overrides"]["tool_policy"]["repo_profile"] = "RP-HYEVE-READONLY-CURRENT"
    overlay = validate_tenant_overlay(payload)
    with pytest.raises(ValueError, match="repo_profile override is not allowed"):
        validate_tenant_overlay_policy(overlay, manifest)


def test_validate_tenant_overlay_policy_blocks_policy_engine_override() -> None:
    manifest = validate_persona_manifest(_minimal_payload())
    payload = _overlay_payload()
    payload["overrides"]["policy_engine_profile"] = "PE-CODER-L2-CURRENT-TENANT"
    overlay = validate_tenant_overlay(payload)
    with pytest.raises(ValueError, match="policy_engine_profile override is not allowed"):
        validate_tenant_overlay_policy(overlay, manifest)


def test_tenant_overlay_schema_contains_required_fields() -> None:
    schema = get_tenant_overlay_schema_current()
    required = set(schema.get("required") or [])
    assert {"schema_version", "tenant_id", "persona_id", "overrides"} <= required


def test_resolve_persona_manifest_is_deterministic() -> None:
    base = validate_persona_manifest(_minimal_payload())
    overlay = validate_tenant_overlay(_overlay_payload())
    result_a, diag_a = resolve_persona_manifest(base_manifest=base, tenant_overlay=overlay)
    result_b, diag_b = resolve_persona_manifest(base_manifest=base, tenant_overlay=overlay)
    assert result_a.model_dump() == result_b.model_dump()
    assert diag_a == diag_b


def test_resolve_persona_manifest_rejects_persona_mismatch() -> None:
    base = validate_persona_manifest(_minimal_payload())
    payload = _overlay_payload()
    payload["persona_id"] = "coder.v2"
    overlay = validate_tenant_overlay(payload)
    with pytest.raises(ValueError, match="persona mismatch"):
        resolve_persona_manifest(base_manifest=base, tenant_overlay=overlay)


def test_resolve_persona_manifest_applies_constraints_add_remove() -> None:
    payload = _minimal_payload()
    payload["constraints"] = ["patch_first", "legacy_constraint"]
    base = validate_persona_manifest(payload)
    overlay_payload = _overlay_payload()
    overlay_payload["overrides"]["constraints_add"] = ["no_auto_done"]
    overlay_payload["overrides"]["constraints_remove"] = ["legacy_constraint"]
    overlay = validate_tenant_overlay(overlay_payload)
    resolved, _diag = resolve_persona_manifest(
        base_manifest=base,
        tenant_overlay=overlay,
        runtime_safe_defaults={"append_constraints": ["force_evidence_pack"]},
    )
    assert "legacy_constraint" not in resolved.constraints
    assert "no_auto_done" in resolved.constraints
    assert "force_evidence_pack" in resolved.constraints


def test_validate_read_only_tool_surface_for_verifier_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/persona_manifests/PM-VERIFIER-RO-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = validate_persona_manifest(payload)
    validate_read_only_tool_surface(manifest)


def test_validate_read_only_tool_surface_rejects_mutation_tools() -> None:
    payload = _minimal_payload()
    payload["tool_policy"]["allowed_tools"] = ["read_file", "apply_patch"]
    manifest = validate_persona_manifest(payload)
    with pytest.raises(ValueError, match="READ_ONLY_TOOL_POLICY_VIOLATION"):
        validate_read_only_tool_surface(manifest)


def test_validate_persona_manifest_coder_l2_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/persona_manifests/PM-CODER-L2-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = validate_persona_manifest(payload)
    assert manifest.persona_id == "coder.current"
    assert manifest.tool_policy.allowed_commands_profile == "ACP-CODER-L2-CURRENT"


def test_validate_persona_manifest_coder_l3_fixture() -> None:
    fixture = Path("projects/AIX/hyeve-aix-service/backend/config/persona_manifests/PM-CODER-L3-CURRENT.json")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = validate_persona_manifest(payload)
    assert manifest.persona_id == "coder.current.l3"
    assert manifest.tool_policy.allowed_commands_profile == "ACP-CODER-L3-CURRENT"
