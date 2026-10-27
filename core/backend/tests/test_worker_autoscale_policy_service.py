from __future__ import annotations

from backend.services.worker_autoscale_policy_service import compute_autoscale_decision


def test_compute_autoscale_decision_scales_up_with_fairness() -> None:
    decision = compute_autoscale_decision(
        tenant_backlog={"tenant-a": 15, "tenant-b": 8, "tenant-c": 2},
        current_workers=1,
        min_workers=1,
        max_workers=10,
        queue_per_worker=5,
    )

    assert decision.action == "scale_up"
    assert decision.target_workers == 5
    assert decision.telemetry["fairness_ok"] is True
    assert decision.per_tenant_allocation["tenant-a"] >= 1
    assert decision.per_tenant_allocation["tenant-b"] >= 1
    assert decision.per_tenant_allocation["tenant-c"] >= 1


def test_compute_autoscale_decision_scales_down_when_backlog_zero() -> None:
    decision = compute_autoscale_decision(
        tenant_backlog={"tenant-a": 0, "tenant-b": 0},
        current_workers=6,
        min_workers=2,
        max_workers=10,
        queue_per_worker=4,
    )

    assert decision.action == "scale_down"
    assert decision.target_workers == 2
    assert decision.telemetry["total_backlog"] == 0
    assert decision.telemetry["fairness_ok"] is True
