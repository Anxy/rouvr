"""Tests for F6-P4 Escalation Hook & Policy Adapter (fail-closed)."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.recovery.escalation import (
    AixEscalationPolicyAdapter,
    EscalationReceipt,
    EscalationRequest,
    NoOpEscalationHook,
)


@pytest.mark.asyncio
async def test_noop_escalation_hook():
    hook = NoOpEscalationHook()
    req = EscalationRequest(
        tenant_id="tenant-1",
        objective_id="obj-100",
        cycle_id="cycle-200",
        reason_code="MAX_RETRIES_EXCEEDED",
    )

    receipt = await hook.notify_operator(req)
    assert receipt.status == "NOOP_IGNORED"
    assert "disabled" in receipt.details["message"]
    assert len(receipt.receipt_id) > 0


@pytest.mark.asyncio
async def test_aix_escalation_policy_adapter_fail_closed_without_service():
    adapter = AixEscalationPolicyAdapter()
    req = EscalationRequest(
        tenant_id="tenant-1",
        objective_id="obj-100",
        cycle_id="cycle-200",
        reason_code="REPLAN_BUDGET_EXHAUSTED",
        severity="CRITICAL",
        command_id="cmd-esc-1",
    )

    receipt1 = await adapter.notify_operator(req)
    assert receipt1.status == "FAILED_NO_SERVICE"
    assert "not configured" in str(receipt1.details.get("error", ""))

    # Failures are not cached as DELIVERED; re-call still fails closed.
    receipt2 = await adapter.notify_operator(req)
    assert receipt2.status == "FAILED_NO_SERVICE"


@pytest.mark.asyncio
async def test_aix_escalation_policy_adapter_delivered_only_on_real_success():
    class OkService:
        def notify_operator(self, request: EscalationRequest) -> dict:
            return {"ok": True, "delivered": True}

    adapter = AixEscalationPolicyAdapter(escalation_service=OkService())
    req = EscalationRequest(
        tenant_id="tenant-1",
        objective_id="obj-100",
        cycle_id="cycle-200",
        reason_code="REPLAN_BUDGET_EXHAUSTED",
        severity="CRITICAL",
        command_id="cmd-esc-2",
    )
    receipt1 = await adapter.notify_operator(req)
    assert receipt1.status == "DELIVERED"
    receipt2 = await adapter.notify_operator(req)
    assert receipt2 == receipt1
    assert receipt2.receipt_id == receipt1.receipt_id


@pytest.mark.asyncio
async def test_aix_escalation_policy_adapter_delivery_failure():
    class BoomService:
        def notify_operator(self, request: EscalationRequest) -> dict:
            raise RuntimeError("smtp down")

    adapter = AixEscalationPolicyAdapter(escalation_service=BoomService())
    req = EscalationRequest(
        tenant_id="t",
        objective_id="o",
        cycle_id="c",
        reason_code="UNRECOVERABLE",
    )
    receipt = await adapter.notify_operator(req)
    assert receipt.status == "FAILED_DELIVERY"


def test_escalation_request_serialization_and_key():
    req1 = EscalationRequest(
        tenant_id="t1",
        objective_id="o1",
        cycle_id="c1",
        reason_code="UNRECOVERABLE_SYNTAX_ERROR",
        command_id="cmd-1",
    )
    req2 = EscalationRequest(
        tenant_id="t1",
        objective_id="o1",
        cycle_id="c1",
        reason_code="UNRECOVERABLE_SYNTAX_ERROR",
        command_id="cmd-1",
    )

    assert req1.idempotency_key() == req2.idempotency_key()

    data = req1.to_dict()
    assert data["reason_code"] == "UNRECOVERABLE_SYNTAX_ERROR"
    assert data["idempotency_key"] == req1.idempotency_key()
