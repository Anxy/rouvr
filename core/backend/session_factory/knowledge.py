"""Durable context & knowledge policy (Stage 5 core).

CodeIntel wire format is reused, never reinvented: a capsule maps to a
``context_pack_document`` KnowledgeArtifact (tenant/project/kind +
summary/body_markdown/metadata_json carrying session/tenant/version).
Three pure policy functions (unit-tested):

- ``capsule_to_artifact``: capsule → artifact-shaped payload.
- ``knowledge_status``: compare a held capsule against a stored
  artifact → current | stale (artifact newer) | ahead (capsule newer,
  needs persist) — revision-awareness without a live service.
- ``tenant_check``: artifact tenant must equal session tenant, else
  refuse (isolation at the policy layer, independent of API auth).

Live CodeIntel reads/writes stay behind the read-by-kind gap
(CODEINTEL-001, owner call); this module is the policy the client
will call through.
"""

from __future__ import annotations

from typing import Any

ARTIFACT_KIND = "context_pack_document"


def capsule_to_artifact(
    capsule: dict[str, Any],
    *,
    project_id: str,
    repo_id: str,
    created_by: str,
) -> dict[str, Any]:
    """Map a capsule to a KnowledgeArtifact-shaped payload (pure)."""
    meta = capsule.get("codeintel_snapshot", {})
    return {
        "tenant_id": capsule["tenant_id"],
        "project_id": project_id,
        "repo_id": repo_id,
        "artifact_kind": ARTIFACT_KIND,
        "title": f"session-capsule {capsule['session_id']}",
        "summary": str(capsule.get("intent", "")),
        "body_markdown": None,
        "source_uri": None,
        "metadata_json": {
            "session_id": capsule["session_id"],
            "tenant_id": capsule["tenant_id"],
            "capsule_version": capsule.get("capsule_version", 1),
            "lane_checkpoint": capsule.get("lane_checkpoint", ""),
            "artifacts": meta.get("artifacts", []),
        },
        "created_by": created_by,
    }


def tenant_check(*, session_tenant: str, artifact_tenant: str) -> bool:
    """True iff the artifact belongs to the session tenant."""
    return bool(session_tenant) and session_tenant == artifact_tenant


def knowledge_status(
    *,
    capsule_version: int,
    artifact_version: int,
) -> str:
    """'current' | 'stale' | 'ahead' by version compare (pure)."""
    if artifact_version > capsule_version:
        return "stale"
    if capsule_version > artifact_version:
        return "ahead"
    return "current"
