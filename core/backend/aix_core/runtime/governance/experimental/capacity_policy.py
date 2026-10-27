"""AIX-E2E-CYCLE-001M-W07: capacity/concurrency.

Distinct from `backpressure.py` (001M-W05, per-tenant admission fairness for queued
work): this module bounds concurrent *execution* against worker-pool and per-model
limits, so one hot tenant or one saturated model route cannot starve the others even
after their work has already been admitted to the queue.
"""

from __future__ import annotations

from dataclasses import dataclass


class CapacityPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CapacityLimitsV1:
    max_concurrent_global: int
    max_concurrent_per_tenant: int
    max_concurrent_per_model: int

    def __post_init__(self) -> None:
        for name, value in (
            ("max_concurrent_global", self.max_concurrent_global),
            ("max_concurrent_per_tenant", self.max_concurrent_per_tenant),
            ("max_concurrent_per_model", self.max_concurrent_per_model),
        ):
            if value <= 0:
                raise CapacityPolicyError(f"{name} must be positive")
        if self.max_concurrent_per_tenant > self.max_concurrent_global:
            raise CapacityPolicyError("max_concurrent_per_tenant must not exceed max_concurrent_global")


class CapacityGate:
    def __init__(self, limits: CapacityLimitsV1) -> None:
        self._limits = limits
        self._global_in_flight = 0
        self._per_tenant: dict[str, int] = {}
        self._per_model: dict[str, int] = {}

    def try_acquire(self, *, tenant_id: str, model_ref: str) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if self._global_in_flight >= self._limits.max_concurrent_global:
            reasons.append("GLOBAL_CONCURRENCY_LIMIT_REACHED")
        if self._per_tenant.get(tenant_id, 0) >= self._limits.max_concurrent_per_tenant:
            reasons.append("TENANT_CONCURRENCY_LIMIT_REACHED")
        if self._per_model.get(model_ref, 0) >= self._limits.max_concurrent_per_model:
            reasons.append("MODEL_CONCURRENCY_LIMIT_REACHED")

        if reasons:
            return False, tuple(reasons)

        self._global_in_flight += 1
        self._per_tenant[tenant_id] = self._per_tenant.get(tenant_id, 0) + 1
        self._per_model[model_ref] = self._per_model.get(model_ref, 0) + 1
        return True, ("ACQUIRED",)

    def release(self, *, tenant_id: str, model_ref: str) -> None:
        if self._global_in_flight <= 0:
            raise CapacityPolicyError("cannot release; no global capacity currently held")
        self._global_in_flight -= 1
        self._per_tenant[tenant_id] = max(0, self._per_tenant.get(tenant_id, 0) - 1)
        self._per_model[model_ref] = max(0, self._per_model.get(model_ref, 0) - 1)

    @property
    def global_in_flight(self) -> int:
        return self._global_in_flight

    def tenant_in_flight(self, tenant_id: str) -> int:
        return self._per_tenant.get(tenant_id, 0)

    def model_in_flight(self, model_ref: str) -> int:
        return self._per_model.get(model_ref, 0)


__all__ = [
    "CapacityPolicyError",
    "CapacityLimitsV1",
    "CapacityGate",
]
