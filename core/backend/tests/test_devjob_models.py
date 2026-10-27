from __future__ import annotations

import pytest

from backend.models.devjob_models import DEVJOB_SCHEMA_VERSION_Current, build_devjob_payload
from backend.models.persona_manifest_models import (
    PERSONA_MANIFEST_SCHEMA_VERSION_Current,
    TENANT_OVERLAY_SCHEMA_VERSION_Current,
)


def _persona_manifest() -> dict:
    return {
        "schema_version": PERSONA_MANIFEST_SCHEMA_VERSION_Current,
        "persona_id": "coder.current",
        "persona_name": "Coder Persona v1",
        "persona_version": "v1",
        "autonomy_cap": "L2",
        "policy_engine_profile": "PE-CODER-L2-CURRENT",
        "tool_policy": {
            "allowed_commands_profile": "ACP-CODER-L2-CURRENT",
            "repo_profile": "RP-HYEVE-PY-FASTAPI-CURRENT",
        },
        "evidence_policy": {
            "evidence_pack_schema": "evidence_pack.current",
            "require_acceptance_mapping": False,
        },
        "constraints": ["patch_first"],
    }


def _tenant_overlay() -> dict:
    return {
        "schema_version": TENANT_OVERLAY_SCHEMA_VERSION_Current,
        "tenant_id": "default",
        "persona_id": "coder.current",
        "overrides": {
            "constraints_add": ["no_auto_done"],
        },
    }


def test_build_devjob_payload_requires_resolved_persona_config() -> None:
    job = build_devjob_payload(
        project_id="bh-migrated-7-aix",
        correlation_id="corr-001",
        persona_manifest_payload=_persona_manifest(),
        tenant_overlay_payload=_tenant_overlay(),
        task_number=658,
    )
    assert job.schema_version == DEVJOB_SCHEMA_VERSION_Current
    assert (
        job.resolved_persona_manifest.policy_engine_profile == "PE-CODER-L2-CURRENT"
    )  # base profile retained; policy_engine_profile overrides are blocked
    assert "no_auto_done" in job.resolved_persona_manifest.constraints
    assert job.resolved_persona_manifest.evidence_policy.require_acceptance_mapping is True


def test_build_devjob_payload_blocks_invalid_persona_payload() -> None:
    payload = _persona_manifest()
    payload["autonomy_cap"] = "L9"
    with pytest.raises(ValueError):
        build_devjob_payload(
            project_id="bh-migrated-7-aix",
            correlation_id="corr-002",
            persona_manifest_payload=payload,
        )


def test_build_devjob_payload_blocks_overlay_persona_mismatch() -> None:
    overlay = _tenant_overlay()
    overlay["persona_id"] = "coder.v2"
    with pytest.raises(ValueError, match="persona mismatch"):
        build_devjob_payload(
            project_id="bh-migrated-7-aix",
            correlation_id="corr-003",
            persona_manifest_payload=_persona_manifest(),
            tenant_overlay_payload=overlay,
        )
