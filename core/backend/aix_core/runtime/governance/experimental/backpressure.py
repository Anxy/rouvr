"""AIX-E2E-CYCLE-001M-W05: queue/backpressure/load shedding.

Per-tenant fairness: no single tenant's backlog may starve every other tenant's queued
work. `TenantFairnessQueue` tracks in-flight counts per tenant against a per-tenant quota
and a global ceiling, and sheds (denies admission to) new work once either is exhausted —
existing admitted work is never evicted by this module; shedding only ever applies to a
new admission request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AdmissionDisposition = Literal["ADMIT", "SHED"]


class BackpressureError(ValueError):
    pass


@dataclass(frozen=True)
class AdmissionDecisionV1:
    disposition: AdmissionDisposition
    reason_codes: tuple[str, ...]


class TenantFairnessQueue:
    def __init__(self, *, per_tenant_quota: int, global_ceiling: int) -> None:
        if per_tenant_quota <= 0 or global_ceiling <= 0:
            raise BackpressureError("per_tenant_quota and global_ceiling must be positive")
        if per_tenant_quota > global_ceiling:
            raise BackpressureError("per_tenant_quota must not exceed global_ceiling")
        self._per_tenant_quota = per_tenant_quota
        self._global_ceiling = global_ceiling
        self._in_flight: dict[str, int] = {}

    @property
    def total_in_flight(self) -> int:
        return sum(self._in_flight.values())

    def in_flight_for(self, tenant_id: str) -> int:
        return self._in_flight.get(tenant_id, 0)

    def try_admit(self, tenant_id: str) -> AdmissionDecisionV1:
        if not tenant_id:
            raise BackpressureError("tenant_id is required")

        reasons: list[str] = []
        if self.in_flight_for(tenant_id) >= self._per_tenant_quota:
            reasons.append("TENANT_QUOTA_EXHAUSTED")
        if self.total_in_flight >= self._global_ceiling:
            reasons.append("GLOBAL_CEILING_REACHED")

        if reasons:
            return AdmissionDecisionV1("SHED", tuple(reasons))

        self._in_flight[tenant_id] = self.in_flight_for(tenant_id) + 1
        return AdmissionDecisionV1("ADMIT", ("WITHIN_QUOTA_AND_CEILING",))

    def release(self, tenant_id: str) -> None:
        current = self.in_flight_for(tenant_id)
        if current <= 0:
            raise BackpressureError(f"cannot release; tenant {tenant_id!r} has no in-flight admission")
        self._in_flight[tenant_id] = current - 1


__all__ = [
    "AdmissionDisposition",
    "BackpressureError",
    "AdmissionDecisionV1",
    "TenantFairnessQueue",
]
