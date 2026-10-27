"""C023: context manifest + provenance for model inputs.

DoD: every model input can be reconstructed without embedding secret values.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

MANIFEST_SCHEMA = "aix.context_manifest.v1"

ContextRole = Literal[
    "objective",
    "approval",
    "plan",
    "evidence",
    "repository",
    "working",
    "system",
]

TrustLevel = Literal["trusted", "untrusted", "secret_ref", "stale"]


class ContextManifestError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_SECRET_PATTERN = re.compile(
    r"(?i)(^|[^A-Za-z0-9])(api[_-]?key|secret|token|password)(\s*[:=]\s*)(\S+)"
)


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def redact_for_model(text: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2\3***REDACTED***", text or "")


@dataclass
class ContextItem:
    item_id: str
    role: ContextRole
    source: str
    content_hash: str
    created_at: str
    trust: TrustLevel = "trusted"
    relevance: float = 1.0
    exclusion_reason: str | None = None
    # Never store raw secrets; redacted preview only for reconstruction metadata.
    redacted_preview: str = ""
    uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "role": self.role,
            "source": self.source,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "trust": self.trust,
            "relevance": self.relevance,
            "exclusion_reason": self.exclusion_reason,
            "redacted_preview": self.redacted_preview,
            "uri": self.uri,
        }


@dataclass
class ContextManifest:
    manifest_id: str
    tenant_id: str
    run_id: str | None
    items: list[ContextItem] = field(default_factory=list)
    schema_version: str = MANIFEST_SCHEMA
    created_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "items": [i.to_dict() for i in self.items],
        }


def add_item(
    manifest: ContextManifest,
    *,
    role: ContextRole,
    source: str,
    content: str,
    trust: TrustLevel = "trusted",
    relevance: float = 1.0,
    uri: str | None = None,
) -> ContextItem:
    raw = content or ""
    if trust != "secret_ref" and _SECRET_PATTERN.search(raw):
        # Fail closed: secret-shaped content must be secret_ref or redacted first.
        raise ContextManifestError("SECRET_EMBEDDED_IN_CONTEXT", source)
    redacted = redact_for_model(raw)
    item = ContextItem(
        item_id=f"ci_{new_ulid()[:12]}",
        role=role,
        source=source,
        content_hash=content_hash(raw if trust == "secret_ref" else redacted),
        created_at=_utc(),
        trust=trust,
        relevance=float(relevance),
        redacted_preview=redacted[:500],
        uri=uri,
    )
    if trust == "untrusted":
        item.exclusion_reason = item.exclusion_reason or "UNTRUSTED_SOURCE"
    if trust == "stale":
        item.exclusion_reason = item.exclusion_reason or "STALE_CONTEXT"
    manifest.items.append(item)
    return item


def exclude_item(manifest: ContextManifest, *, item_id: str, reason: str) -> ContextItem:
    for item in manifest.items:
        if item.item_id == item_id:
            item.exclusion_reason = reason
            item.relevance = 0.0
            return item
    raise ContextManifestError("CONTEXT_ITEM_NOT_FOUND", item_id)


def assemble_prompt_parts(manifest: ContextManifest) -> list[dict[str, str]]:
    """Assemble model-facing parts from included items only — never raw secrets."""
    parts: list[dict[str, str]] = []
    for item in sorted(manifest.items, key=lambda i: (-i.relevance, i.created_at)):
        if item.exclusion_reason:
            continue
        if item.trust == "secret_ref":
            parts.append(
                {
                    "role": item.role,
                    "source": item.source,
                    "text": f"[SECRET_REF:{item.uri or item.item_id}]",
                    "content_hash": item.content_hash,
                }
            )
            continue
        if item.trust in {"untrusted", "stale"}:
            continue
        parts.append(
            {
                "role": item.role,
                "source": item.source,
                "text": item.redacted_preview,
                "content_hash": item.content_hash,
            }
        )
    return parts


def reconstruct_manifest(raw: Mapping[str, Any]) -> ContextManifest:
    if str(raw.get("schema_version") or "") != MANIFEST_SCHEMA:
        raise ContextManifestError("UNKNOWN_MANIFEST_SCHEMA", str(raw.get("schema_version")))
    items = [
        ContextItem(
            item_id=str(i["item_id"]),
            role=i["role"],  # type: ignore[arg-type]
            source=str(i["source"]),
            content_hash=str(i["content_hash"]),
            created_at=str(i["created_at"]),
            trust=i.get("trust", "trusted"),  # type: ignore[arg-type]
            relevance=float(i.get("relevance") or 0),
            exclusion_reason=i.get("exclusion_reason"),
            redacted_preview=str(i.get("redacted_preview") or ""),
            uri=i.get("uri"),
        )
        for i in (raw.get("items") or [])
    ]
    return ContextManifest(
        manifest_id=str(raw["manifest_id"]),
        tenant_id=str(raw["tenant_id"]),
        run_id=raw.get("run_id"),
        items=items,
        created_at=str(raw.get("created_at") or _utc()),
    )


def new_manifest(*, tenant_id: str, run_id: str | None = None) -> ContextManifest:
    if not tenant_id.strip():
        raise ContextManifestError("EMPTY_TENANT")
    return ContextManifest(
        manifest_id=f"cm_{new_ulid()[:12]}",
        tenant_id=tenant_id,
        run_id=run_id,
    )


def manifest_fingerprint(manifest: ContextManifest) -> str:
    payload = json.dumps(manifest.to_dict(), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "MANIFEST_SCHEMA",
    "ContextRole",
    "TrustLevel",
    "ContextManifestError",
    "ContextItem",
    "ContextManifest",
    "content_hash",
    "redact_for_model",
    "add_item",
    "exclude_item",
    "assemble_prompt_parts",
    "reconstruct_manifest",
    "new_manifest",
    "manifest_fingerprint",
]
