"""AIX Feature 12 (Packet 02) — VerificationResultV1 contract."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

VerificationScope = Literal["STEP", "FEATURE", "INTEGRATION", "OBJECTIVE"]
VerificationStatus = Literal["PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"]
ProofStatus = Literal["PASS", "FAIL", "NOT_RUN"]


def _compute_sha256(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OVItemResultV1(BaseModel):
    ov_id: str
    status: VerificationStatus
    expected: str = ""
    observed: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    probe_output: str | None = None
    test_results: dict[str, Any] | None = None


class VerificationResultV1(BaseModel):
    verification_id: str
    objective_id: str
    scope: VerificationScope
    ov_item_results: list[OVItemResultV1] = Field(default_factory=list)
    static_proof_status: ProofStatus = "NOT_RUN"
    runtime_proof_status: ProofStatus = "NOT_RUN"
    test_evidence: list[str] = Field(default_factory=list)
    blocking_failures: list[str] = Field(default_factory=list)
    verifier: dict[str, str] = Field(
        default_factory=lambda: {"role": "verifier", "model": "governed"}
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    evidence_bundle_hash: str | None = None

    @property
    def is_pass(self) -> bool:
        if self.blocking_failures:
            return False
        if self.scope == "OBJECTIVE":
            return self.runtime_proof_status == "PASS"
        if self.ov_item_results:
            return all(r.status in ("PASS", "NOT_APPLICABLE") for r in self.ov_item_results)
        return self.static_proof_status == "PASS"


__all__ = [
    "VerificationScope",
    "VerificationStatus",
    "ProofStatus",
    "OVItemResultV1",
    "VerificationResultV1",
]
