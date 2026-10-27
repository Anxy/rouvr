"""AIX Feature 12 (Packet 02) — VerificationDecisionV1 contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

VerificationDecisionKind = Literal[
    "PASS",
    "REPAIR",
    "HALT",
    "BLOCKED",
    "NEEDS_RUNTIME_PROOF",
]


class VerificationDecisionV1(BaseModel):
    decision_id: str
    verification_id: str
    objective_id: str
    decision: VerificationDecisionKind
    reasoning: str
    blocking_gaps: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    decision_model: str = "governed_verifier"
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


__all__ = [
    "VerificationDecisionKind",
    "VerificationDecisionV1",
]
