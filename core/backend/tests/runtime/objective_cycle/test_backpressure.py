"""AIX-E2E-CYCLE-001M-W05 queue/backpressure/load shedding tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.backpressure import (
    BackpressureError,
    TenantFairnessQueue,
)


class TestConstruction:
    def test_non_positive_quota_rejected(self) -> None:
        with pytest.raises(BackpressureError):
            TenantFairnessQueue(per_tenant_quota=0, global_ceiling=10)

    def test_tenant_quota_exceeding_global_rejected(self) -> None:
        with pytest.raises(BackpressureError):
            TenantFairnessQueue(per_tenant_quota=20, global_ceiling=10)


class TestAdmission:
    def test_admits_within_quota(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=2, global_ceiling=10)
        decision = queue.try_admit("tenant-1")
        assert decision.disposition == "ADMIT"

    def test_sheds_when_tenant_quota_exhausted(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=1, global_ceiling=10)
        queue.try_admit("tenant-1")
        decision = queue.try_admit("tenant-1")
        assert decision.disposition == "SHED"
        assert "TENANT_QUOTA_EXHAUSTED" in decision.reason_codes

    def test_one_hot_tenant_cannot_starve_others_below_global_ceiling(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=3, global_ceiling=10)
        for _ in range(3):
            assert queue.try_admit("hot-tenant").disposition == "ADMIT"
        # hot-tenant is now shed by its own quota, but tenant-2 is unaffected.
        assert queue.try_admit("hot-tenant").disposition == "SHED"
        assert queue.try_admit("tenant-2").disposition == "ADMIT"

    def test_sheds_when_global_ceiling_reached_even_under_tenant_quota(self) -> None:
        # per_tenant_quota == global_ceiling here (the constructor forbids quota >
        # ceiling); tenant-3's own quota is nowhere near exhausted, but the global
        # ceiling is, which is the scenario this test targets.
        queue = TenantFairnessQueue(per_tenant_quota=2, global_ceiling=2)
        queue.try_admit("tenant-1")
        queue.try_admit("tenant-2")
        decision = queue.try_admit("tenant-3")
        assert decision.disposition == "SHED"
        assert "GLOBAL_CEILING_REACHED" in decision.reason_codes
        assert "TENANT_QUOTA_EXHAUSTED" not in decision.reason_codes

    def test_release_frees_capacity_for_new_admission(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=1, global_ceiling=10)
        queue.try_admit("tenant-1")
        assert queue.try_admit("tenant-1").disposition == "SHED"
        queue.release("tenant-1")
        assert queue.try_admit("tenant-1").disposition == "ADMIT"

    def test_release_without_in_flight_work_rejected(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=1, global_ceiling=10)
        with pytest.raises(BackpressureError):
            queue.release("tenant-1")

    def test_shedding_never_evicts_existing_admissions(self) -> None:
        queue = TenantFairnessQueue(per_tenant_quota=1, global_ceiling=1)
        queue.try_admit("tenant-1")
        queue.try_admit("tenant-2")  # shed
        assert queue.in_flight_for("tenant-1") == 1
