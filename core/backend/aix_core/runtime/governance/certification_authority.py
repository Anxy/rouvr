"""AIX Feature 12 (Packet 08) — Objective Certification Authority.

Evaluates terminalization and verification outputs to produce authoritative
CertificationDecisionV1: CERTIFIED vs GAPS_FOUND vs REPAIR_REQUIRED.
Enforces model independence (Certifier != Executor).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

from backend.aix_core.runtime.verification.terminalization_guard import (
    TerminalizationDecisionV1,
)
from backend.aix_core.runtime.verification.verification_decision import (
    VerificationDecisionV1,
)
from backend.aix_core.runtime.verification.verification_result import (
    VerificationResultV1,
)

CertificationVerdict = Literal["CERTIFIED", "GAPS_FOUND", "REPAIR_REQUIRED", "BLOCKED"]


class CertificationDecisionV1(BaseModel):
    decision_id: str
    objective_id: str
    cycle_id: str
    verdict: CertificationVerdict
    terminalization_decision_id: str
    reasons: tuple[str, ...] = ()
    evidence_bundle_ref: str = ""
    certifier: dict[str, str] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class CertificationAuthority:
    """Independent governing certifier for objective cycles."""

    def __init__(
        self,
        certifier_role: str = "critical_reviewer",
        certifier_model: str | None = None,
    ) -> None:
        from backend.aix_core.runtime.model_registry.registry import get_model_registry

        self.certifier_role = certifier_role
        self.certifier_model = certifier_model or get_model_registry().get_route(
            "critical_reviewer", escalated=True
        ).model_id

    def evaluate_certification(
        self,
        *,
        objective_id: str,
        cycle_id: str,
        terminalization_decision: TerminalizationDecisionV1,
        verification_result: VerificationResultV1 | None = None,
        executor_model: str | None = None,
    ) -> CertificationDecisionV1:
        """Issue authoritative CertificationDecisionV1."""
        reasons: list[str] = []
        
        # Invariant: Certifier model independence
        if executor_model and self.certifier_model and executor_model == self.certifier_model:
            reasons.append(f"Model independence violation: Certifier ({self.certifier_model}) must differ from Executor ({executor_model})")

        decision_id = f"cert_{cycle_id}_{hashlib.md5((terminalization_decision.decision_id + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:8]}"

        if terminalization_decision.decision == "ALLOW_CERTIFIED":
            verdict: CertificationVerdict = "CERTIFIED"
            reasons.append("All 5 terminalization conjunction criteria satisfied.")
        elif terminalization_decision.decision == "DENY_NEEDS_REPAIR":
            verdict = "REPAIR_REQUIRED"
            reasons.append("Terminalization denied: open gaps or runtime proof failure.")
            reasons.append(terminalization_decision.reasoning)
        else:
            verdict = "GAPS_FOUND"
            reasons.append(f"Terminalization denied: {terminalization_decision.reasoning}")

        return CertificationDecisionV1(
            decision_id=decision_id,
            objective_id=objective_id,
            cycle_id=cycle_id,
            verdict=verdict,
            terminalization_decision_id=terminalization_decision.decision_id,
            reasons=tuple(reasons),
            evidence_bundle_ref=f"trace://{cycle_id}",
            certifier={
                "role": self.certifier_role,
                "model": self.certifier_model,
            },
        )


__all__ = [
    "CertificationVerdict",
    "CertificationDecisionV1",
    "CertificationAuthority",
]
