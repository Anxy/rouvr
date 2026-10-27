"""AIX-W06-E01-T001: Content-Addressed Artifact Registry.

Content-addressed store for cycle evidence payloads (test outputs, diffs, execution logs).
Computes sha256 digests, redacts secrets using SecretBoundary, and enforces integrity contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.governance.secret_boundary import redact_objective_text


@dataclass(frozen=True)
class ArtifactRecord:
    digest: str  # sha256 hex digest
    media_type: str  # e.g. "application/json", "text/x-diff", "text/plain"
    size_bytes: int
    producer: str
    attempt_id: str
    snapshot_id: str
    retention_policy: str = "PERMANENT"
    integrity_verified: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))


class ArtifactRegistryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class ContentAddressedArtifactRegistry:
    def __init__(self, store_dir: Path) -> None:
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, ArtifactRecord] = {}

    def put_artifact(
        self,
        payload: str | bytes,
        *,
        media_type: str,
        producer: str,
        attempt_id: str,
        snapshot_id: str,
        retention_policy: str = "PERMANENT",
    ) -> ArtifactRecord:
        if isinstance(payload, str):
            # Redact secrets before storing text payloads
            redacted_str, _ = redact_objective_text(payload)
            data_bytes = redacted_str.encode("utf-8")
        else:
            data_bytes = payload

        digest = hashlib.sha256(data_bytes).hexdigest()
        sub_dir = self.store_dir / digest[:2]
        sub_dir.mkdir(parents=True, exist_ok=True)

        content_file = sub_dir / digest
        meta_file = sub_dir / f"{digest}.meta.json"

        if not content_file.exists():
            content_file.write_bytes(data_bytes)

        record = ArtifactRecord(
            digest=digest,
            media_type=media_type,
            size_bytes=len(data_bytes),
            producer=producer,
            attempt_id=attempt_id,
            snapshot_id=snapshot_id,
            retention_policy=retention_policy,
            integrity_verified=True,
        )

        meta_file.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
        self._index[digest] = record
        return record

    def get_artifact(self, digest: str) -> tuple[ArtifactRecord, bytes]:
        sub_dir = self.store_dir / digest[:2]
        content_file = sub_dir / digest
        meta_file = sub_dir / f"{digest}.meta.json"

        if not content_file.exists() or not meta_file.exists():
            raise ArtifactRegistryError("ARTIFACT_NOT_FOUND", digest)

        data_bytes = content_file.read_bytes()
        computed = hashlib.sha256(data_bytes).hexdigest()
        if computed != digest:
            raise ArtifactRegistryError("INTEGRITY_CORRUPTED", f"expected {digest}, got {computed}")

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        record = ArtifactRecord(**meta)
        return record, data_bytes


__all__ = [
    "ArtifactRecord",
    "ArtifactRegistryError",
    "ContentAddressedArtifactRegistry",
]
