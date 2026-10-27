"""Deterministic worker autoscaling policy with tenant fairness constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AutoscaleDecision:
    target_workers: int
    action: str
    reasons: list[str]
    per_tenant_allocation: dict[str, int]
    telemetry: dict[str, Any]


def compute_autoscale_decision(
    *,
    tenant_backlog: dict[str, int],
    current_workers: int,
    min_workers: int = 1,
    max_workers: int = 20,
    queue_per_worker: int = 10,
    min_share_per_active_tenant: int = 1,
) -> AutoscaleDecision:
    safe_current = max(0, int(current_workers))
    safe_min = max(1, int(min_workers))
    safe_max = max(safe_min, int(max_workers))
    total_backlog = sum(max(0, int(v)) for v in tenant_backlog.values())
    required_workers = (total_backlog + max(1, queue_per_worker) - 1) // max(1, queue_per_worker)
    target = max(safe_min, min(safe_max, required_workers))

    active_tenants = [tenant for tenant, backlog in tenant_backlog.items() if int(backlog) > 0]
    reasons: list[str] = []
    if target > safe_current:
        action = "scale_up"
        reasons.append("QUEUE_PRESSURE_ABOVE_CAPACITY")
    elif target < safe_current:
        action = "scale_down"
        reasons.append("QUEUE_PRESSURE_BELOW_CAPACITY")
    else:
        action = "hold"
        reasons.append("CAPACITY_BALANCED")

    allocation: dict[str, int] = {tenant: 0 for tenant in tenant_backlog}
    if active_tenants:
        base_reserved = min(target, len(active_tenants) * max(1, min_share_per_active_tenant))
        for tenant in active_tenants:
            allocation[tenant] = 1 if base_reserved > 0 else 0
        remaining = max(0, target - sum(allocation.values()))
        if remaining > 0:
            weighted = sorted(active_tenants, key=lambda t: int(tenant_backlog.get(t, 0)), reverse=True)
            idx = 0
            while remaining > 0 and weighted:
                allocation[weighted[idx % len(weighted)]] += 1
                remaining -= 1
                idx += 1

    fairness_ok = True
    starving_tenants: list[str] = []
    for tenant in active_tenants:
        if allocation.get(tenant, 0) < 1:
            fairness_ok = False
            starving_tenants.append(tenant)
    if not fairness_ok:
        reasons.append("TENANT_FAIRNESS_VIOLATION")
    else:
        reasons.append("TENANT_FAIRNESS_OK")

    telemetry = {
        "current_workers": safe_current,
        "target_workers": target,
        "min_workers": safe_min,
        "max_workers": safe_max,
        "queue_per_worker": max(1, queue_per_worker),
        "total_backlog": total_backlog,
        "active_tenants": len(active_tenants),
        "fairness_ok": fairness_ok,
        "starving_tenants": starving_tenants,
    }
    return AutoscaleDecision(
        target_workers=target,
        action=action,
        reasons=reasons,
        per_tenant_allocation=allocation,
        telemetry=telemetry,
    )
