"""Deterministic, token-capped context capsules for agent dispatch."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONTEXT_CAPSULE_SCHEMA_VERSION = "aix.context-capsule.v1"


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    provenance_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(min_length=1)
    required: bool = False
    priority: int = Field(default=0, ge=0, le=100)


class CapsuleItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    source_uri: str
    provenance_record_sha256: str
    content: str
    estimated_tokens: int = Field(ge=1)


class ContextCapsule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CONTEXT_CAPSULE_SCHEMA_VERSION
    tenant_id: str
    repository_id: str
    commit_sha: str
    max_tokens: int
    estimated_tokens: int
    items: tuple[CapsuleItem, ...]
    omitted_item_ids: tuple[str, ...]
    capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _normalize_content(content: str) -> str:
    return " ".join(str(content).split())


def estimate_tokens(content: str) -> int:
    """Stable conservative approximation used until tokenizer routing lands."""

    return max(1, math.ceil(len(content.encode("utf-8")) / 4))


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ContextCapsuleBuilder:
    def build(
        self,
        *,
        tenant_id: str,
        repository_id: str,
        commit_sha: str,
        items: list[ContextItem],
        max_tokens: int,
    ) -> ContextCapsule:
        normalized_tenant = str(tenant_id).strip()
        normalized_repository = str(repository_id).strip()
        normalized_commit = str(commit_sha).strip()
        if not normalized_tenant or not normalized_repository or len(normalized_commit) < 7:
            raise ValueError("tenant_id, repository_id and commit_sha are required")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if not items:
            raise ValueError("at least one context item is required")
        item_ids = [item.item_id for item in items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("DUPLICATE_CONTEXT_ITEM_ID")

        ranked = sorted(items, key=lambda item: (not item.required, -item.priority, item.item_id))
        seen_content: set[str] = set()
        selected: list[CapsuleItem] = []
        omitted: list[str] = []
        used_tokens = 0
        for item in ranked:
            content = _normalize_content(item.content)
            content_identity = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_identity in seen_content:
                omitted.append(item.item_id)
                continue
            token_count = estimate_tokens(content)
            if used_tokens + token_count > max_tokens:
                if item.required:
                    raise ValueError(
                        f"REQUIRED_CONTEXT_EXCEEDS_TOKEN_CAP item_id={item.item_id} "
                        f"required={used_tokens + token_count} cap={max_tokens}"
                    )
                omitted.append(item.item_id)
                continue
            selected.append(
                CapsuleItem(
                    item_id=item.item_id,
                    source_uri=item.source_uri,
                    provenance_record_sha256=item.provenance_record_sha256,
                    content=content,
                    estimated_tokens=token_count,
                )
            )
            used_tokens += token_count
            seen_content.add(content_identity)

        if not selected:
            raise ValueError("CONTEXT_CAPSULE_EMPTY")
        payload: dict[str, Any] = {
            "schema_version": CONTEXT_CAPSULE_SCHEMA_VERSION,
            "tenant_id": normalized_tenant,
            "repository_id": normalized_repository,
            "commit_sha": normalized_commit,
            "max_tokens": max_tokens,
            "estimated_tokens": used_tokens,
            "items": [item.model_dump(mode="json") for item in selected],
            "omitted_item_ids": sorted(omitted),
        }
        return ContextCapsule(**payload, capsule_sha256=_hash_payload(payload))
