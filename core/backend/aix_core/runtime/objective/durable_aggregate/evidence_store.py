"""C015: content-addressed durable evidence references.

DoD: replay and completion resolve the same immutable evidence after restart.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping


EVIDENCE_SCHEMA = "aix.evidence.v1"


class EvidenceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def content_hash(payload: Mapping[str, Any] | bytes | str) -> str:
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass
class EvidenceRecord:
    evidence_id: str
    tenant_id: str
    run_id: str
    content_hash: str
    uri: str
    payload: dict[str, Any]
    created_at: str
    redacted: bool = False
    retained_until: str | None = None
    schema_version: str = EVIDENCE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "content_hash": self.content_hash,
            "uri": self.uri,
            "payload": deepcopy(self.payload),
            "created_at": self.created_at,
            "redacted": self.redacted,
            "retained_until": self.retained_until,
        }


class InMemoryEvidenceStore:
    """Content-addressed evidence; share across backends for restart sims."""

    def __init__(self) -> None:
        self._by_id: dict[tuple[str, str], EvidenceRecord] = {}
        self._by_hash: dict[tuple[str, str], str] = {}

    def put(
        self,
        *,
        tenant_id: str,
        run_id: str,
        payload: Mapping[str, Any],
        evidence_id: str | None = None,
        uri_prefix: str = "evidence://",
    ) -> EvidenceRecord:
        if not tenant_id.strip():
            raise EvidenceError("EMPTY_TENANT")
        digest = content_hash(payload)
        existing_id = self._by_hash.get((tenant_id, digest))
        if existing_id is not None:
            existing = self._by_id[(tenant_id, existing_id)]
            return existing
        eid = evidence_id or digest.replace("sha256:", "ev_")[:19]
        record = EvidenceRecord(
            evidence_id=eid,
            tenant_id=tenant_id,
            run_id=run_id,
            content_hash=digest,
            uri=f"{uri_prefix}{eid}",
            payload=deepcopy(dict(payload)),
            created_at=_utc(),
        )
        self._by_id[(tenant_id, eid)] = record
        self._by_hash[(tenant_id, digest)] = eid
        return record

    def get(self, *, tenant_id: str, evidence_id: str) -> EvidenceRecord:
        rec = self._by_id.get((tenant_id, evidence_id))
        if rec is None:
            raise EvidenceError("EVIDENCE_NOT_FOUND", evidence_id)
        return rec

    def verify(self, *, tenant_id: str, evidence_id: str) -> EvidenceRecord:
        rec = self.get(tenant_id=tenant_id, evidence_id=evidence_id)
        actual = content_hash(rec.payload)
        if actual != rec.content_hash:
            raise EvidenceError("EVIDENCE_TAMPERED", evidence_id)
        return rec

    def redact(self, *, tenant_id: str, evidence_id: str, keys: tuple[str, ...] = ("secret", "token", "password")) -> EvidenceRecord:
        rec = self.get(tenant_id=tenant_id, evidence_id=evidence_id)
        redacted_payload = {
            k: ("[REDACTED]" if any(s in k.lower() for s in keys) else v)
            for k, v in rec.payload.items()
        }
        # Redaction creates a *new* content-addressed object; original remains immutable.
        return self.put(
            tenant_id=tenant_id,
            run_id=rec.run_id,
            payload={**redacted_payload, "_redacted_from": rec.evidence_id},
            evidence_id=f"{rec.evidence_id}_redacted",
        )

    def resolve_many(self, *, tenant_id: str, evidence_ids: list[str]) -> list[EvidenceRecord]:
        out: list[EvidenceRecord] = []
        for eid in evidence_ids:
            out.append(self.verify(tenant_id=tenant_id, evidence_id=eid))
        return out


__all__ = [
    "EVIDENCE_SCHEMA",
    "EvidenceError",
    "EvidenceRecord",
    "InMemoryEvidenceStore",
    "content_hash",
]
