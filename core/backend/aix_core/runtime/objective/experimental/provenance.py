"""Append-only context provenance records with tenant and hash-chain checks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

PROVENANCE_SCHEMA_VERSION = "aix.context-provenance.v1"
GENESIS_HASH = "0" * 64


class ContextAuthority(StrEnum):
    CODEINTEL = "CODEINTEL"
    WORKHUB = "WORKHUB"
    HUMAN = "HUMAN"
    REPOSITORY_POLICY = "REPOSITORY_POLICY"


class ContextProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PROVENANCE_SCHEMA_VERSION
    sequence: int = Field(ge=1)
    item_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    authority: ContextAuthority
    observed_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_id: str | None = None
    commit_sha: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    previous_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _record_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


class ContextProvenanceLedger:
    """Bounded in-process ledger; Parent B supplies the durable repository."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: list[ContextProvenanceRecord] = []
        self._item_ids: set[tuple[str, str]] = set()

    def append(
        self,
        *,
        item_id: str,
        tenant_id: str,
        source_uri: str,
        authority: ContextAuthority,
        content: bytes | str,
        repository_id: str | None = None,
        commit_sha: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextProvenanceRecord:
        normalized_item_id = str(item_id).strip()
        normalized_tenant_id = str(tenant_id).strip()
        normalized_source_uri = str(source_uri).strip()
        if not normalized_item_id or not normalized_tenant_id or not normalized_source_uri:
            raise ValueError("item_id, tenant_id and source_uri are required")
        item_key = (normalized_tenant_id, normalized_item_id)
        if item_key in self._item_ids:
            raise ValueError("DUPLICATE_CONTEXT_ITEM")
        observed_at = self._clock()
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        observed_at = observed_at.astimezone(UTC)
        raw_content = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        previous_hash = self._records[-1].record_sha256 if self._records else GENESIS_HASH
        hash_payload = {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "sequence": len(self._records) + 1,
            "item_id": normalized_item_id,
            "tenant_id": normalized_tenant_id,
            "source_uri": normalized_source_uri,
            "authority": authority.value,
            "observed_at": observed_at.isoformat(),
            "content_sha256": _content_hash(raw_content),
            "repository_id": str(repository_id).strip() if repository_id else None,
            "commit_sha": str(commit_sha).strip() if commit_sha else None,
            "metadata": dict(metadata or {}),
            "previous_record_sha256": previous_hash,
        }
        record = ContextProvenanceRecord(
            **hash_payload,
            record_sha256=_record_hash(hash_payload),
        )
        self._records.append(record)
        self._item_ids.add(item_key)
        return record

    def list_for_tenant(self, tenant_id: str) -> tuple[ContextProvenanceRecord, ...]:
        normalized = str(tenant_id).strip()
        if not normalized:
            raise ValueError("tenant_id is required")
        return tuple(record for record in self._records if record.tenant_id == normalized)

    def verify(self) -> bool:
        previous_hash = GENESIS_HASH
        for expected_sequence, record in enumerate(self._records, start=1):
            payload = record.model_dump(mode="json", exclude={"record_sha256"})
            payload["authority"] = record.authority.value
            payload["observed_at"] = record.observed_at.isoformat()
            if record.sequence != expected_sequence:
                return False
            if record.previous_record_sha256 != previous_hash:
                return False
            if _record_hash(payload) != record.record_sha256:
                return False
            previous_hash = record.record_sha256
        return True

    @property
    def records(self) -> tuple[ContextProvenanceRecord, ...]:
        return tuple(self._records)
