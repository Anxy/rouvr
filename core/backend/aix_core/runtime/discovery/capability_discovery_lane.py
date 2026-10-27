"""P26: Governed Capability Discovery.

Pipeline: need → missing capability → candidate discovery → trust assessment
→ sandbox → adapter → candidate registration → tests → evaluation
→ policy assignment → approval → activation.

Critical invariant: Discovery never implies authorization. Unknown/discovered
capabilities remain unavailable until full activation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

DiscoveryStage = Literal[
    "need_identified", "candidate_found", "trust_assessed",
    "sandboxed", "adapter_built", "registered",
    "tested", "evaluated", "policy_assigned",
    "pending_approval", "approved", "activated", "rejected",
]


@dataclass
class DiscoveredCapabilityV1:
    discovery_id: str
    need: str
    capability_id: str
    adapter_type: str
    stage: DiscoveryStage = "need_identified"
    trust_level: str = "untrusted"
    test_results: dict[str, Any] = field(default_factory=dict)
    eval_run_id: str | None = None
    policy_assigned: bool = False
    approved_by: str | None = None
    rejected_reason: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )


class CapabilityDiscoveryLane:
    """Governed discovery pipeline. Discovery never implies authorization."""

    def __init__(self) -> None:
        self._discoveries: dict[str, DiscoveredCapabilityV1] = {}
        self._activated: set[str] = set()

    def identify_need(self, need: str, *, capability_id: str, adapter_type: str) -> DiscoveredCapabilityV1:
        did = f"disc-{uuid.uuid4().hex[:12]}"
        d = DiscoveredCapabilityV1(
            discovery_id=did, need=need,
            capability_id=capability_id,
            adapter_type=adapter_type,
        )
        self._discoveries[did] = d
        return d

    def assess_trust(self, discovery_id: str, *, trust_level: str) -> None:
        d = self._discoveries.get(discovery_id)
        if d is None:
            raise ValueError("discovery not found")
        d.trust_level = trust_level
        d.stage = "trust_assessed"

    def sandbox_test(self, discovery_id: str, *, passed: bool, details: dict[str, Any] | None = None) -> None:
        d = self._discoveries.get(discovery_id)
        if d is None:
            raise ValueError("discovery not found")
        d.test_results = details or {"passed": passed}
        d.stage = "tested" if passed else "rejected"
        if not passed:
            d.rejected_reason = "sandbox test failed"

    def assign_policy(self, discovery_id: str) -> None:
        d = self._discoveries.get(discovery_id)
        if d is None:
            raise ValueError("discovery not found")
        if d.stage == "rejected":
            raise ValueError("cannot assign policy to rejected capability")
        d.policy_assigned = True
        d.stage = "policy_assigned"

    def request_activation(self, discovery_id: str) -> DiscoveredCapabilityV1 | None:
        d = self._discoveries.get(discovery_id)
        if d is None:
            return None
        if not d.policy_assigned:
            d.rejected_reason = "no policy assigned"
            d.stage = "rejected"
            return d
        if d.stage == "rejected":
            return d
        d.stage = "pending_approval"
        return d

    def approve_activation(self, discovery_id: str, *, approved_by: str) -> bool:
        d = self._discoveries.get(discovery_id)
        if d is None or d.stage != "pending_approval":
            return False
        d.approved_by = approved_by
        d.stage = "approved"
        return True

    def activate(self, discovery_id: str) -> bool:
        d = self._discoveries.get(discovery_id)
        if d is None or d.stage != "approved":
            return False
        d.stage = "activated"
        self._activated.add(d.capability_id)
        return True

    def is_available(self, capability_id: str) -> bool:
        return capability_id in self._activated

    def get(self, discovery_id: str) -> DiscoveredCapabilityV1 | None:
        return self._discoveries.get(discovery_id)


__all__ = [
    "DiscoveryStage", "DiscoveredCapabilityV1", "CapabilityDiscoveryLane",
]
