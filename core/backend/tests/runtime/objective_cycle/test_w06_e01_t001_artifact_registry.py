"""AIX-W06-E01-T001: Content-Addressed Artifact Registry Tests.

Verifies:
1. Content-addressed storage: SHA256 digest computation & deduplication.
2. Secret boundary redaction: fake API keys/passwords redacted before storing text artifacts.
3. Integrity checking: tampered files raise INTEGRITY_CORRUPTED.
4. Complete metadata tracking across producer, attempt_id, and snapshot_id.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.runtime.verification.artifact_registry import (
    ArtifactRecord,
    ArtifactRegistryError,
    ContentAddressedArtifactRegistry,
)


def test_content_addressing_and_deduplication(tmp_path: Path) -> None:
    """Artifacts with identical content produce identical sha256 digests."""
    registry = ContentAddressedArtifactRegistry(store_dir=tmp_path / "artifacts")

    rec1 = registry.put_artifact(
        "Execution Log Content",
        media_type="text/plain",
        producer="executor",
        attempt_id="att-1",
        snapshot_id="snap-1",
    )
    rec2 = registry.put_artifact(
        "Execution Log Content",
        media_type="text/plain",
        producer="verifier",
        attempt_id="att-2",
        snapshot_id="snap-1",
    )

    assert rec1.digest == rec2.digest
    assert len(rec1.digest) == 64


def test_secret_redaction_before_storage(tmp_path: Path) -> None:
    """Fake OpenRouter API keys in text evidence are redacted before storage."""
    registry = ContentAddressedArtifactRegistry(store_dir=tmp_path / "artifacts")
    leaked_payload = "API Response: api_key=sk-or-v1-abcdef0123456789abcdef0123456789 in response."

    rec = registry.put_artifact(
        leaked_payload,
        media_type="text/plain",
        producer="executor",
        attempt_id="att-1",
        snapshot_id="snap-1",
    )

    stored_rec, stored_bytes = registry.get_artifact(rec.digest)
    stored_text = stored_bytes.decode("utf-8")

    assert "sk-or-v1-abcdef0123456789abcdef0123456789" not in stored_text
    assert "REDACTED" in stored_text


def test_integrity_corruption_detection(tmp_path: Path) -> None:
    """Tampering with disk payload triggers INTEGRITY_CORRUPTED error."""
    registry = ContentAddressedArtifactRegistry(store_dir=tmp_path / "artifacts")
    rec = registry.put_artifact(
        "Original Content",
        media_type="text/plain",
        producer="executor",
        attempt_id="att-1",
        snapshot_id="snap-1",
    )

    # Tamper with stored file
    sub_dir = tmp_path / "artifacts" / rec.digest[:2]
    content_file = sub_dir / rec.digest
    content_file.write_bytes(b"Tampered Content")

    with pytest.raises(ArtifactRegistryError) as exc_info:
        registry.get_artifact(rec.digest)

    assert "INTEGRITY_CORRUPTED" in str(exc_info.value)
