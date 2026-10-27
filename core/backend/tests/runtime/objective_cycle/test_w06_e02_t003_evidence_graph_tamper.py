"""AIX-W06-E02-T003: Evidence Graph & Tamper Test.

Verifies:
1. Complete binding: criterion -> assertion -> verification -> evidence -> producer -> snapshot.
2. Cryptographic chain hashing over all evidence nodes.
3. Tamper detection: modifying or substituting evidence nodes fails closed with EVIDENCE_GRAPH_TAMPERED.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.verification.evidence_graph import (
    EvidenceGraphChain,
    EvidenceGraphError,
    EvidenceNode,
)


def test_evidence_graph_chain_binding() -> None:
    """Verifies complete evidence node binding across criterion, assertion, verification, digest, producer, snapshot."""
    chain = EvidenceGraphChain()

    node1 = EvidenceNode(
        criterion_id="crit-1",
        assertion_id="assert-diff-exists",
        verification_id="ver-run-101",
        artifact_digest="a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
        producer_id="patch_engine",
        snapshot_id="snap-2026-08-17",
    )
    chain.append(node1)

    digest = chain.compute_chain_digest()
    assert len(digest) == 64

    # Validating intact chain passes
    chain.validate_chain_integrity(digest)


def test_evidence_graph_tamper_detection() -> None:
    """Tampering with an evidence node or attempting to substitute a digest fails closed."""
    chain = EvidenceGraphChain()

    node1 = EvidenceNode(
        criterion_id="crit-1",
        assertion_id="assert-diff-exists",
        verification_id="ver-run-101",
        artifact_digest="a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
        producer_id="patch_engine",
        snapshot_id="snap-2026-08-17",
    )
    chain.append(node1)

    original_digest = chain.compute_chain_digest()

    # Tampered chain with substituted digest
    tampered_chain = EvidenceGraphChain()
    tampered_node = EvidenceNode(
        criterion_id="crit-1",
        assertion_id="assert-diff-exists",
        verification_id="ver-run-101",
        artifact_digest="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",  # tampered!
        producer_id="patch_engine",
        snapshot_id="snap-2026-08-17",
    )
    tampered_chain.append(tampered_node)

    with pytest.raises(EvidenceGraphError) as exc_info:
        tampered_chain.validate_chain_integrity(original_digest)

    assert "EVIDENCE_GRAPH_TAMPERED" in str(exc_info.value)
