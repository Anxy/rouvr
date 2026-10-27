from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models.persona_manifest_models import (
    PERSONA_MANIFEST_SCHEMA_VERSION_Current,
    PersonaManifestCurrent,
    TENANT_OVERLAY_SCHEMA_VERSION_Current,
    TenantOverlayCurrent,
    validate_persona_manifest,
    validate_tenant_overlay,
)
from backend.services.persona_overlay_resolver_service import resolve_persona_manifest

DEVJOB_SCHEMA_VERSION_Current = "aix_devjob.current"


class DevJobCurrent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=DEVJOB_SCHEMA_VERSION_Current, frozen=True)
    project_id: str = Field(..., min_length=1)
    correlation_id: str = Field(..., min_length=1)
    task_number: int | None = Field(default=None, ge=1)
    persona_manifest: PersonaManifestCurrent
    tenant_overlay: TenantOverlayCurrent | None = None
    resolved_persona_manifest: PersonaManifestCurrent
    resolver_diagnostics: dict[str, Any] = Field(default_factory=dict)


def build_devjob_payload(
    *,
    project_id: str,
    correlation_id: str,
    persona_manifest_payload: dict[str, Any],
    tenant_overlay_payload: dict[str, Any] | None = None,
    task_number: int | None = None,
) -> DevJobCurrent:
    manifest = validate_persona_manifest(persona_manifest_payload)
    overlay = None
    if tenant_overlay_payload is not None:
        overlay = validate_tenant_overlay(tenant_overlay_payload)
        if overlay.schema_version != TENANT_OVERLAY_SCHEMA_VERSION_Current:
            raise ValueError("Unsupported tenant overlay schema version for DevJob.")
    if manifest.schema_version != PERSONA_MANIFEST_SCHEMA_VERSION_Current:
        raise ValueError("Unsupported persona manifest schema version for DevJob.")

    resolved_manifest, diagnostics = resolve_persona_manifest(
        base_manifest=manifest,
        tenant_overlay=overlay,
        runtime_safe_defaults={"require_acceptance_mapping": True},
    )
    return DevJobCurrent(
        project_id=project_id,
        correlation_id=correlation_id,
        task_number=task_number,
        persona_manifest=manifest,
        tenant_overlay=overlay,
        resolved_persona_manifest=resolved_manifest,
        resolver_diagnostics=diagnostics,
    )
