"""001N-W04: Build/release provenance — reproducible artifact identity."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProvenanceRecordV1:
    artifact_id: str
    git_sha: str
    created_at: str
    sbom_digest: str
    signature_status: str
    attestation_refs: tuple[str, ...]
    immutable: bool = True


def digest_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_provenance(
    *,
    git_sha: str,
    files: list[Path],
    attestation_refs: tuple[str, ...] = (),
    signed: bool = False,
) -> ProvenanceRecordV1:
    file_digests = []
    for path in files:
        if path.exists() and path.is_file():
            file_digests.append({str(path): hashlib.sha256(path.read_bytes()).hexdigest()})
    sbom = digest_payload({"files": file_digests, "git_sha": git_sha})
    artifact_id = digest_payload({"sbom": sbom, "git_sha": git_sha})[:32]
    return ProvenanceRecordV1(
        artifact_id=artifact_id,
        git_sha=git_sha,
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        sbom_digest=sbom,
        signature_status="SIGNED" if signed else "UNSIGNED_LOCAL",
        attestation_refs=attestation_refs,
        immutable=True,
    )
