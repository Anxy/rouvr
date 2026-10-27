"""AIX-E2E-CYCLE-001M-W07 capacity/concurrency tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.capacity_policy import (
    CapacityGate,
    CapacityLimitsV1,
    CapacityPolicyError,
)


def _limits(**overrides: object) -> CapacityLimitsV1:
    base = dict(max_concurrent_global=10, max_concurrent_per_tenant=3, max_concurrent_per_model=5)
    base.update(overrides)
    return CapacityLimitsV1(**base)


class TestLimitsValidation:
    def test_non_positive_limit_rejected(self) -> None:
        with pytest.raises(CapacityPolicyError):
            _limits(max_concurrent_global=0)

    def test_tenant_limit_exceeding_global_rejected(self) -> None:
        with pytest.raises(CapacityPolicyError):
            _limits(max_concurrent_per_tenant=20, max_concurrent_global=10)


class TestAcquireRelease:
    def test_acquire_within_limits(self) -> None:
        gate = CapacityGate(_limits())
        ok, reasons = gate.try_acquire(tenant_id="t1", model_ref="model-a")
        assert ok is True
        assert gate.global_in_flight == 1

    def test_tenant_limit_denies_further_acquisition(self) -> None:
        gate = CapacityGate(_limits(max_concurrent_per_tenant=1))
        gate.try_acquire(tenant_id="t1", model_ref="model-a")
        ok, reasons = gate.try_acquire(tenant_id="t1", model_ref="model-b")
        assert ok is False
        assert "TENANT_CONCURRENCY_LIMIT_REACHED" in reasons

    def test_model_limit_denies_further_acquisition(self) -> None:
        gate = CapacityGate(_limits(max_concurrent_per_model=1, max_concurrent_per_tenant=10))
        gate.try_acquire(tenant_id="t1", model_ref="model-a")
        ok, reasons = gate.try_acquire(tenant_id="t2", model_ref="model-a")
        assert ok is False
        assert "MODEL_CONCURRENCY_LIMIT_REACHED" in reasons

    def test_global_limit_denies_even_within_tenant_and_model_limits(self) -> None:
        # max_concurrent_per_tenant must not exceed max_concurrent_global, so both are 1
        # here; max_concurrent_per_model is left generous (5) so the model axis is not
        # what denies the second request — only the global ceiling is.
        gate = CapacityGate(_limits(max_concurrent_global=1, max_concurrent_per_tenant=1, max_concurrent_per_model=5))
        gate.try_acquire(tenant_id="t1", model_ref="model-a")
        ok, reasons = gate.try_acquire(tenant_id="t2", model_ref="model-b")
        assert ok is False
        assert "GLOBAL_CONCURRENCY_LIMIT_REACHED" in reasons
        assert "TENANT_CONCURRENCY_LIMIT_REACHED" not in reasons
        assert "MODEL_CONCURRENCY_LIMIT_REACHED" not in reasons

    def test_one_hot_tenant_cannot_consume_whole_global_pool(self) -> None:
        gate = CapacityGate(_limits(max_concurrent_global=10, max_concurrent_per_tenant=2))
        gate.try_acquire(tenant_id="hot", model_ref="m1")
        gate.try_acquire(tenant_id="hot", model_ref="m2")
        ok, reasons = gate.try_acquire(tenant_id="hot", model_ref="m3")
        assert ok is False  # hot tenant capped at 2 even though global has room for 10
        # another tenant is unaffected
        ok2, _ = gate.try_acquire(tenant_id="other", model_ref="m4")
        assert ok2 is True

    def test_release_frees_all_three_counters(self) -> None:
        gate = CapacityGate(_limits(max_concurrent_per_tenant=1, max_concurrent_per_model=1))
        gate.try_acquire(tenant_id="t1", model_ref="model-a")
        gate.release(tenant_id="t1", model_ref="model-a")
        assert gate.global_in_flight == 0
        assert gate.tenant_in_flight("t1") == 0
        assert gate.model_in_flight("model-a") == 0

    def test_release_without_capacity_held_rejected(self) -> None:
        gate = CapacityGate(_limits())
        with pytest.raises(CapacityPolicyError):
            gate.release(tenant_id="t1", model_ref="model-a")
