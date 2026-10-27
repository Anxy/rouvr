"""AIX-W06-E02-T003: Immutable Evidence Graph.

Binds criterion -> assertion -> verification -> evidence -> producer -> snapshot in an immutable,
cryptographically hashed evidence graph chain. Prevents deletion or silent substitution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


class EvidenceGraphError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EvidenceNode:
    criterion_id: str
    assertion_id: str
    verification_id: str
    artifact_digest: str
    producer_id: str
    snapshot_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    def compute_node_hash(self) -> str:
        payload = f"{self.criterion_id}:{self.assertion_id}:{self.verification_id}:{self.artifact_digest}:{self.producer_id}:{self.snapshot_id}:{self.timestamp}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EvidenceGraphChain:
    def __init__(self) -> None:
        self._nodes: list[EvidenceNode] = []

    def append(self, node: EvidenceNode) -> None:
        self._nodes.append(node)

    def compute_chain_digest(self) -> str:
        if not self._nodes:
            return hashlib.sha256(b"EMPTY_EVIDENCE_GRAPH").hexdigest()
        hashes = [node.compute_node_hash() for node in self._nodes]
        combined = "|".join(hashes)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def validate_chain_integrity(self, expected_digest: str) -> None:
        actual = self.compute_chain_digest()
        if actual != expected_digest:
            raise EvidenceGraphError("EVIDENCE_GRAPH_TAMPERED", f"expected {expected_digest}, got {actual}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_digest": self.compute_chain_digest(),
            "nodes": [asdict(n) for n in self._nodes],
        }


__all__ = ["EvidenceNode", "EvidenceGraphChain", "EvidenceGraphError"]
