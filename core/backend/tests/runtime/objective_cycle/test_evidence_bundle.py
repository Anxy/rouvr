"""AIX-E2E-CYCLE-001H-W03 evidence bundle tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.evidence_bundle import (
    UNSIGNED_MARKER,
    EvidenceBundleError,
    EvidenceItemV1,
    build_bundle,
    verify_bundle_integrity,
)


def _items() -> list[EvidenceItemV1]:
    return [
        EvidenceItemV1(ref="evidence://diff", content_hash="a" * 64),
        EvidenceItemV1(ref="evidence://test-log", content_hash="b" * 64),
    ]


class TestEvidenceItem:
    def test_empty_ref_rejected(self) -> None:
        with pytest.raises(EvidenceBundleError):
            EvidenceItemV1(ref="", content_hash="a" * 64)

    def test_empty_hash_rejected(self) -> None:
        with pytest.raises(EvidenceBundleError):
            EvidenceItemV1(ref="evidence://x", content_hash="")


class TestBuildBundle:
    def test_basic_bundle(self) -> None:
        bundle = build_bundle(attempt_id="attempt-1", candidate_sha="deadbeef", items=_items())
        assert bundle.signature == UNSIGNED_MARKER
        assert bundle.is_signed is False
        assert len(bundle.bundle_hash) == 64

    def test_empty_items_rejected(self) -> None:
        with pytest.raises(EvidenceBundleError):
            build_bundle(attempt_id="attempt-1", candidate_sha="deadbeef", items=[])

    def test_missing_attempt_id_rejected(self) -> None:
        with pytest.raises(EvidenceBundleError):
            build_bundle(attempt_id="", candidate_sha="deadbeef", items=_items())

    def test_hash_is_order_independent(self) -> None:
        a = build_bundle(attempt_id="attempt-1", candidate_sha="sha1", items=_items())
        b = build_bundle(attempt_id="attempt-1", candidate_sha="sha1", items=list(reversed(_items())))
        assert a.bundle_hash == b.bundle_hash

    def test_different_candidate_sha_changes_hash(self) -> None:
        a = build_bundle(attempt_id="attempt-1", candidate_sha="sha1", items=_items())
        b = build_bundle(attempt_id="attempt-1", candidate_sha="sha2", items=_items())
        assert a.bundle_hash != b.bundle_hash

    def test_signer_applied_when_provided(self) -> None:
        bundle = build_bundle(
            attempt_id="attempt-1",
            candidate_sha="sha1",
            items=_items(),
            signer=lambda h: f"sig:{h[:8]}",
        )
        assert bundle.signature.startswith("sig:")
        assert bundle.is_signed is True


class TestIntegrity:
    def test_verify_bundle_integrity_passes_for_untouched_bundle(self) -> None:
        bundle = build_bundle(attempt_id="attempt-1", candidate_sha="sha1", items=_items())
        assert verify_bundle_integrity(bundle) is True

    def test_verify_bundle_integrity_fails_for_hand_tampered_bundle(self) -> None:
        bundle = build_bundle(attempt_id="attempt-1", candidate_sha="sha1", items=_items())
        # frozen dataclass: simulate tampering by constructing a new instance that
        # claims the old hash but has different items.
        from dataclasses import replace

        tampered = replace(bundle, items=(EvidenceItemV1(ref="evidence://forged", content_hash="c" * 64),))
        assert verify_bundle_integrity(tampered) is False
